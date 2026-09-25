"""Temporal workflow definition for the role/location change process.

``update_hr_record`` runs first, the three downstream steps that only depend
on it run concurrently, then ``notify_downstream`` closes the run. Retry of
transient failures and non-retry of permanent ones is delegated to Temporal
retry policies instead of the legacy hand-rolled loop.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from .activities import RoleChangeActivities
    from .contracts import (
        MAX_ATTEMPTS,
        PARALLEL_STEPS,
        PERMANENT_ERROR,
        STEP_ORDER,
        WORKFLOW_FAILED_ERROR,
        RoleChangeInput,
        RoleChangeResult,
        RunOutcome,
        StepInput,
        WorkflowProgress,
    )


RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=0.5),
    backoff_coefficient=4.0,
    maximum_interval=timedelta(seconds=5),
    maximum_attempts=MAX_ATTEMPTS,
    non_retryable_error_types=[PERMANENT_ERROR],
)
STEP_OPTS = {
    "start_to_close_timeout": timedelta(seconds=30),
    "schedule_to_close_timeout": timedelta(minutes=2),
    "retry_policy": RETRY,
}
BOOKKEEPING_OPTS = {
    "start_to_close_timeout": timedelta(seconds=10),
    "retry_policy": RetryPolicy(maximum_attempts=5),
}


@workflow.defn(name="RoleChangeWorkflow")
class RoleChangeWorkflow:
    def __init__(self) -> None:
        self._run_id: int | None = None
        self._context: dict = {}
        self._current: list[str] = []
        self._completed: list[str] = []
        self._failed: list[str] = []
        self._attempts: dict[str, int] = {}
        self._status = "RUNNING"

    @workflow.run
    async def run(self, input: RoleChangeInput) -> RoleChangeResult:
        ref = await workflow.execute_activity(
            RoleChangeActivities.create_run, input, **BOOKKEEPING_OPTS
        )
        self._run_id = ref.run_id
        workflow.logger.info("run=%s starting role change", ref.run_id)
        try:
            await self._step("update_hr_record")
            results = await asyncio.gather(
                *(self._step(name) for name in PARALLEL_STEPS),
                return_exceptions=True,
            )
            for name, result in zip(PARALLEL_STEPS, results):
                if isinstance(result, BaseException):
                    raise result
            await self._step("notify_downstream")
        except ActivityError as exc:
            self._status = "FAILED"
            step = self._failed[-1] if self._failed else "unknown"
            cause = exc.cause
            message = getattr(cause, "message", None) or str(cause or exc)
            await workflow.execute_activity(
                RoleChangeActivities.mark_run_failed,
                RunOutcome(
                    run_id=ref.run_id,
                    status="FAILED",
                    last_error=f"{step}: {message}",
                ),
                **BOOKKEEPING_OPTS,
            )
            workflow.logger.error(
                "run=%s failed at %s: %s", ref.run_id, step, message
            )
            raise ApplicationError(
                f"run {ref.run_id} failed at {step}: {message}",
                RunOutcome(
                    run_id=ref.run_id,
                    status="FAILED",
                    last_error=f"{step}: {message}",
                ),
                type=WORKFLOW_FAILED_ERROR,
                non_retryable=True,
            ) from exc
        await workflow.execute_activity(
            RoleChangeActivities.mark_run_completed, ref, **BOOKKEEPING_OPTS
        )
        self._status = "COMPLETED"
        workflow.logger.info("run=%s completed", ref.run_id)
        return RoleChangeResult(run_id=ref.run_id, context=dict(self._context))

    async def _step(self, name: str) -> None:
        self._current.append(name)
        try:
            out = await workflow.execute_activity(
                RoleChangeActivities.run_step,
                StepInput(
                    run_id=self._run_id,
                    step=name,
                    context=dict(self._context),
                ),
                **STEP_OPTS,
            )
        except ActivityError:
            self._failed.append(name)
            raise
        finally:
            self._current.remove(name)
        self._attempts[name] = out.attempts
        self._completed.append(name)
        self._context.update(out.result)

    @workflow.query
    def progress(self) -> WorkflowProgress:
        order = {name: index for index, name in enumerate(STEP_ORDER)}
        return WorkflowProgress(
            run_id=self._run_id,
            current_steps=sorted(self._current, key=order.__getitem__),
            completed=sorted(self._completed, key=order.__getitem__),
            failed=list(self._failed),
            attempts=dict(self._attempts),
            status=self._status,
        )
