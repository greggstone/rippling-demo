"""Temporal activities that wrap the existing step functions and ORM state.

All activities are synchronous because they use the Django ORM; the worker
runs them in a ``ThreadPoolExecutor``. Each activity opens and closes its
database connections so worker threads never leak connections.
"""

import functools
from collections.abc import Callable
from typing import Any

from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..external_systems import (
    ExternalSystems,
    PermanentServiceError,
    TransientServiceError,
)
from ..models import WorkflowRun, WorkflowStepRun
from ..steps import STEP_SEQUENCE
from .contracts import (
    MAX_ATTEMPTS,
    PERMANENT_ERROR,
    TRANSIENT_ERROR,
    RoleChangeInput,
    RunOutcome,
    RunRef,
    StepInput,
    StepOutput,
)

STEP_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = dict(STEP_SEQUENCE)


def _with_clean_connections(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        close_old_connections()
        try:
            return fn(*args, **kwargs)
        finally:
            close_old_connections()

    return wrapper


class RoleChangeActivities:
    """Activity bundle bound to a set of external system clients."""

    def __init__(self, systems: ExternalSystems):
        self.systems = systems

    @activity.defn(name="create_run")
    @_with_clean_connections
    def create_run(self, input: RoleChangeInput) -> RunRef:
        """Create (or adopt) the WorkflowRun plus its step rows.

        Idempotent: a retried execution sees the run already RUNNING and
        leaves it alone; a new execution of a FAILED run counts as a new
        attempt via the FAILED -> RUNNING transition.
        """
        with transaction.atomic():
            run, created = WorkflowRun.objects.get_or_create(
                request_id=input.request_id
            )
            if created:
                for sequence, name in enumerate(STEP_HANDLERS, start=1):
                    WorkflowStepRun.objects.create(
                        run=run,
                        name=name,
                        sequence=sequence,
                        idempotency_key=f"rcr-{run.request_id}-{name}",
                    )
                run.status = WorkflowRun.Status.RUNNING
                run.attempt = 1
                run.last_error = ""
                run.started_at = timezone.now()
                run.save()
            else:
                resumed = WorkflowRun.objects.filter(
                    pk=run.pk, status=WorkflowRun.Status.FAILED
                ).update(
                    status=WorkflowRun.Status.RUNNING,
                    last_error="",
                    attempt=F("attempt") + 1,
                )
                if not resumed:
                    # Retried activity execution, or a run already in flight.
                    WorkflowRun.objects.filter(pk=run.pk).update(
                        status=WorkflowRun.Status.RUNNING, last_error=""
                    )
        activity.logger.info("run=%s created=%s", run.pk, created)
        return RunRef(run_id=run.pk)

    @activity.defn(name="run_step")
    @_with_clean_connections
    def run_step(self, input: StepInput) -> StepOutput:
        """Execute one workflow step exactly as the legacy orchestrator did."""
        handler = STEP_HANDLERS[input.step]
        run = WorkflowRun.objects.select_related("request").get(pk=input.run_id)
        step = run.steps.get(name=input.step)

        # At-least-once delivery safety: never re-execute a completed step.
        if step.status == WorkflowStepRun.Status.COMPLETED:
            activity.logger.info(
                "run=%s step=%s already completed, skipping", run.pk, step.name
            )
            return StepOutput(
                step=step.name,
                result=step.result,
                attempts=step.attempts,
                skipped=True,
            )

        # The workflow's context is the source of truth between steps.
        run.context = dict(input.context)
        run.current_step = step.name
        run.save(update_fields=["context", "current_step", "updated_at"])

        step.status = WorkflowStepRun.Status.RUNNING
        step.started_at = step.started_at or timezone.now()
        step.attempts += 1
        step.save()

        try:
            result = handler(run, step, self.systems)
        except TransientServiceError as exc:
            step.last_error = str(exc)
            if activity.info().attempt >= MAX_ATTEMPTS:
                step.status = WorkflowStepRun.Status.FAILED
            step.save()
            activity.logger.warning(
                "run=%s step=%s transient failure (attempt %s): %s",
                run.pk,
                step.name,
                step.attempts,
                exc,
            )
            raise ApplicationError(str(exc), type=TRANSIENT_ERROR) from exc
        except PermanentServiceError as exc:
            step.status = WorkflowStepRun.Status.FAILED
            step.last_error = str(exc)
            step.save()
            activity.logger.error(
                "run=%s step=%s permanent failure: %s", run.pk, step.name, exc
            )
            raise ApplicationError(
                str(exc), type=PERMANENT_ERROR, non_retryable=True
            ) from exc

        step.status = WorkflowStepRun.Status.COMPLETED
        step.result = result
        step.last_error = ""
        step.finished_at = timezone.now()
        step.save()
        # Merge the step result into run context without clobbering keys
        # written by sibling steps running concurrently.
        with transaction.atomic():
            locked = WorkflowRun.objects.select_for_update().get(pk=run.pk)
            locked.context = {**locked.context, **result}
            locked.save(update_fields=["context", "updated_at"])
        activity.logger.info("run=%s step=%s completed", run.pk, step.name)
        return StepOutput(step=step.name, result=result, attempts=step.attempts)

    @activity.defn(name="mark_run_failed")
    @_with_clean_connections
    def mark_run_failed(self, outcome: RunOutcome) -> None:
        run = WorkflowRun.objects.get(pk=outcome.run_id)
        run.status = WorkflowRun.Status.FAILED
        run.last_error = outcome.last_error
        run.save()
        activity.logger.error(
            "run=%s failed: %s", run.pk, outcome.last_error
        )

    @activity.defn(name="mark_run_completed")
    @_with_clean_connections
    def mark_run_completed(self, ref: RunRef) -> None:
        run = WorkflowRun.objects.get(pk=ref.run_id)
        run.status = WorkflowRun.Status.COMPLETED
        run.current_step = ""
        run.finished_at = timezone.now()
        run.save()
        activity.logger.info("run=%s completed", run.pk)
