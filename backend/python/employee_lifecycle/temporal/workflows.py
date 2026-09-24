"""Durable orchestration of the role/location change.

The workflow only sequences activities; it does no I/O itself. Temporal owns
the state of the run (which activities completed, which are retrying), so
there is no application-level retry loop or resume path: transient provider
failures are retried by the activity retry policy, and a workflow that fails
after exhausting retries can simply be started again for the same request
with all completed steps skipped by the activities' idempotency guards.

Step dependencies::

    update_hr_record
        ├── recalculate_eligibility ┐
        ├── update_payroll          ├── notify_downstream
        └── reconcile_access        ┘
"""

import asyncio
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

from . import contracts
from .contracts import (
    FinishRunInput,
    RoleChangeInput,
    RoleChangeResult,
    SaveContextInput,
    StepFailure,
    StepInput,
    WorkflowProgress,
)


@workflow.defn(name=contracts.WORKFLOW_NAME)
class RoleChangeWorkflow:
    def __init__(self) -> None:
        self._run_id: int | None = None
        self._phase = "pending"
        self._completed: list[str] = []
        self._in_flight: list[str] = []
        self._failures: list[StepFailure] = []

    @workflow.run
    async def run(self, inp: RoleChangeInput) -> RoleChangeResult:
        self._phase = "create_run"
        self._run_id = await workflow.execute_activity(
            contracts.CREATE_RUN,
            inp,
            start_to_close_timeout=contracts.BOOKKEEPING_TIMEOUT,
            retry_policy=contracts.BOOKKEEPING_RETRY,
        )
        run_id: int = self._run_id
        context: dict[str, Any] = {}

        self._phase = contracts.UPDATE_HR_RECORD
        await self._settle(run_id, [contracts.UPDATE_HR_RECORD], context)

        self._phase = "fan_out"
        await self._settle(run_id, list(contracts.FAN_OUT_STEPS), context)

        # notify_downstream reads the merged results of the earlier steps from
        # the run row, so persist them first.
        await workflow.execute_activity(
            contracts.SAVE_CONTEXT,
            SaveContextInput(run_id=run_id, context=context),
            start_to_close_timeout=contracts.BOOKKEEPING_TIMEOUT,
            retry_policy=contracts.BOOKKEEPING_RETRY,
        )

        self._phase = contracts.NOTIFY_DOWNSTREAM
        await self._settle(run_id, [contracts.NOTIFY_DOWNSTREAM], context)

        await self._finish(run_id, context, "COMPLETED")
        self._phase = "completed"
        workflow.logger.info("run=%s completed", run_id)
        return RoleChangeResult(run_id=run_id, status="COMPLETED", context=context)

    @workflow.query
    def progress(self) -> WorkflowProgress:
        return WorkflowProgress(
            run_id=self._run_id,
            phase=self._phase,
            completed_steps=list(self._completed),
            in_flight_steps=list(self._in_flight),
            failures=list(self._failures),
        )

    # -- internals ----------------------------------------------------------

    async def _settle(
        self, run_id: int, steps: list[str], context: dict[str, Any]
    ) -> None:
        """Run ``steps`` concurrently, wait for all of them, merge results into ``context``.

        Independent steps keep running even when a sibling fails, so the
        outside world is never left with an activity still in flight when the
        run is marked FAILED. Once everything has settled, any failure fails
        the workflow.
        """
        outcomes = await asyncio.gather(
            *(self._step(run_id, step) for step in steps), return_exceptions=True
        )

        for step, outcome in zip(steps, outcomes):
            if isinstance(outcome, BaseException):
                self._failures.append(_describe_failure(step, outcome))
            else:
                context.update(outcome)

        if self._failures:
            self._phase = "failed"
            await self._finish(run_id, context, "FAILED")
            summary = "; ".join(f"{f.step}: {f.message}" for f in self._failures)
            raise ApplicationError(
                f"run {run_id} failed at {summary}",
                *self._failures,
                type=contracts.WORKFLOW_FAILED_ERROR,
                non_retryable=True,
            )

    async def _step(self, run_id: int, step: str) -> dict[str, Any]:
        workflow.logger.info("run=%s step=%s starting", run_id, step)
        self._in_flight.append(step)
        try:
            result = await workflow.execute_activity(
                step,
                StepInput(run_id=run_id, step=step),
                start_to_close_timeout=contracts.REMOTE_STEP_TIMEOUT,
                retry_policy=contracts.REMOTE_STEP_RETRY,
            )
        finally:
            self._in_flight.remove(step)
        self._completed.append(step)
        return result

    async def _finish(self, run_id: int, context: dict[str, Any], status: str) -> None:
        await workflow.execute_activity(
            contracts.FINISH_RUN,
            FinishRunInput(
                run_id=run_id, status=status, context=context, failures=list(self._failures)
            ),
            start_to_close_timeout=contracts.BOOKKEEPING_TIMEOUT,
            retry_policy=contracts.BOOKKEEPING_RETRY,
        )


def _describe_failure(step: str, exc: BaseException) -> StepFailure:
    cause = exc.cause if isinstance(exc, ActivityError) else exc
    if isinstance(cause, ApplicationError):
        return StepFailure(step=step, error_type=cause.type or "", message=cause.message)
    return StepFailure(step=step, error_type=type(cause).__name__, message=str(cause))
