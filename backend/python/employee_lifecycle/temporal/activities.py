"""Temporal activities for the role/location change workflow.

Every side effect (local DB writes and remote calls) lives here. The step
handlers themselves are the ones in ``employee_lifecycle.steps``; the
activities wrap them with the bookkeeping the legacy orchestrator used to do
inline: marking the ``WorkflowStepRun`` row, counting attempts and
translating simulated provider errors into Temporal error types.

Idempotency: a step activity first checks the ``WorkflowStepRun`` row. If the
row is already COMPLETED the stored result is returned without touching the
outside world, so re-running a workflow for the same request (after it
failed part way through) never repeats a side effect. Remote calls keep using
the per-request/per-step idempotency keys the providers dedupe on.
"""

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..external_systems import (
    ExternalSystems,
    PermanentServiceError,
    TransientServiceError,
)
from ..models import RoleChangeRequest, WorkflowRun, WorkflowStepRun
from ..steps import STEP_SEQUENCE
from . import contracts
from .contracts import FinishRunInput, RoleChangeInput, SaveContextInput, StepInput

logger = logging.getLogger(__name__)

STEP_HANDLERS = dict(STEP_SEQUENCE)


class RoleChangeActivities:
    def __init__(self, systems: ExternalSystems):
        self.systems = systems

    def all(self) -> list:
        return [
            self.create_run,
            self.update_hr_record,
            self.recalculate_eligibility,
            self.update_payroll,
            self.reconcile_access,
            self.notify_downstream,
            self.save_context,
            self.finish_run,
        ]

    # -- bookkeeping ---------------------------------------------------------

    @activity.defn(name=contracts.CREATE_RUN)
    def create_run(self, inp: RoleChangeInput) -> int:
        """Create the run and step rows, or return the existing run for the request."""
        request = RoleChangeRequest.objects.get(pk=inp.request_id)
        with transaction.atomic():
            run, created = WorkflowRun.objects.get_or_create(request=request)
            if created:
                for sequence, name in enumerate(contracts.STEP_ORDER, start=1):
                    WorkflowStepRun.objects.create(
                        run=run,
                        name=name,
                        sequence=sequence,
                        idempotency_key=contracts.idempotency_key(request.pk, name),
                    )
            run.status = WorkflowRun.Status.RUNNING
            run.attempt += 1
            run.last_error = ""
            run.started_at = run.started_at or timezone.now()
            run.save()
        activity.logger.info("run=%s %s", run.pk, "created" if created else "reused")
        return run.pk

    @activity.defn(name=contracts.SAVE_CONTEXT)
    def save_context(self, inp: SaveContextInput) -> None:
        WorkflowRun.objects.filter(pk=inp.run_id).update(
            context=inp.context, updated_at=timezone.now()
        )

    @activity.defn(name=contracts.FINISH_RUN)
    def finish_run(self, inp: FinishRunInput) -> None:
        run = WorkflowRun.objects.get(pk=inp.run_id)
        run.status = inp.status
        run.context = inp.context
        run.current_step = inp.failures[0].step if inp.failures else ""
        run.last_error = "; ".join(f"{f.step}: {f.message}" for f in inp.failures)
        if inp.status == WorkflowRun.Status.COMPLETED:
            run.finished_at = timezone.now()
        run.save()
        for failure in inp.failures:
            run.steps.filter(name=failure.step).update(
                status=WorkflowStepRun.Status.FAILED, last_error=failure.message
            )
        activity.logger.info("run=%s %s", run.pk, run.status.lower())

    # -- steps ---------------------------------------------------------------

    @activity.defn(name=contracts.UPDATE_HR_RECORD)
    def update_hr_record(self, inp: StepInput) -> dict[str, Any]:
        return self._execute_step(inp)

    @activity.defn(name=contracts.RECALCULATE_ELIGIBILITY)
    def recalculate_eligibility(self, inp: StepInput) -> dict[str, Any]:
        return self._execute_step(inp)

    @activity.defn(name=contracts.UPDATE_PAYROLL)
    def update_payroll(self, inp: StepInput) -> dict[str, Any]:
        return self._execute_step(inp)

    @activity.defn(name=contracts.RECONCILE_ACCESS)
    def reconcile_access(self, inp: StepInput) -> dict[str, Any]:
        return self._execute_step(inp)

    @activity.defn(name=contracts.NOTIFY_DOWNSTREAM)
    def notify_downstream(self, inp: StepInput) -> dict[str, Any]:
        return self._execute_step(inp)

    def _execute_step(self, inp: StepInput) -> dict[str, Any]:
        run = WorkflowRun.objects.select_related("request").get(pk=inp.run_id)
        step = run.steps.get(name=inp.step)
        if step.status == WorkflowStepRun.Status.COMPLETED:
            activity.logger.info("run=%s step=%s already completed, skipping", run.pk, step.name)
            return step.result

        step.status = WorkflowStepRun.Status.RUNNING
        step.attempts += 1
        step.started_at = step.started_at or timezone.now()
        step.save()
        WorkflowRun.objects.filter(pk=run.pk).update(current_step=step.name)

        handler = STEP_HANDLERS[step.name]
        try:
            result = handler(run, step, self.systems)
        except TransientServiceError as exc:
            step.last_error = str(exc)
            step.save(update_fields=["last_error"])
            activity.logger.warning(
                "run=%s step=%s transient failure (attempt %s): %s",
                run.pk, step.name, activity.info().attempt, exc,
            )
            raise ApplicationError(str(exc), type=contracts.TRANSIENT_ERROR) from exc
        except PermanentServiceError as exc:
            step.status = WorkflowStepRun.Status.FAILED
            step.last_error = str(exc)
            step.save()
            activity.logger.error("run=%s step=%s permanent failure: %s", run.pk, step.name, exc)
            raise ApplicationError(
                str(exc), type=contracts.PERMANENT_ERROR, non_retryable=True
            ) from exc

        step.status = WorkflowStepRun.Status.COMPLETED
        step.result = result
        step.last_error = ""
        step.finished_at = timezone.now()
        step.save()
        activity.logger.info("run=%s step=%s completed", run.pk, step.name)
        return result
