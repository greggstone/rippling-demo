"""Sequential orchestrator for the role/location change workflow.

The orchestrator walks ``STEP_SEQUENCE`` in order, persisting state to
``WorkflowRun``/``WorkflowStepRun`` between steps. Remote failures are retried
in-process with a fixed backoff schedule; when retries are exhausted the run is
marked FAILED and can later be picked up again with ``resume``.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from django.db import transaction
from django.utils import timezone

from .external_systems import (
    ExternalSystems,
    PermanentServiceError,
    TransientServiceError,
)
from .models import RoleChangeRequest, WorkflowRun, WorkflowStepRun
from .steps import STEP_SEQUENCE

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (0.5, 2.0, 5.0)


class WorkflowFailed(Exception):
    def __init__(self, run: WorkflowRun, step: WorkflowStepRun, cause: Exception):
        super().__init__(f"run {run.pk} failed at {step.name}: {cause}")
        self.run = run
        self.step = step
        self.cause = cause


class RoleChangeOrchestrator:
    def __init__(
        self,
        systems: ExternalSystems,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = MAX_ATTEMPTS,
    ):
        self.systems = systems
        self.sleep = sleep
        self.max_attempts = max_attempts

    # -- public API ---------------------------------------------------------

    def start(self, request: RoleChangeRequest) -> WorkflowRun:
        """Create the run and its step rows, then execute from the top."""
        with transaction.atomic():
            run = WorkflowRun.objects.create(request=request)
            for sequence, (name, _handler) in enumerate(STEP_SEQUENCE, start=1):
                WorkflowStepRun.objects.create(
                    run=run,
                    name=name,
                    sequence=sequence,
                    idempotency_key=f"rcr-{request.pk}-{name}",
                )
        return self._execute(run)

    def resume(self, run: WorkflowRun) -> WorkflowRun:
        """Continue a FAILED run from the first step that has not completed."""
        if run.status == WorkflowRun.Status.COMPLETED:
            logger.info("run=%s already completed; nothing to resume", run.pk)
            return run
        if run.status == WorkflowRun.Status.RUNNING:
            raise ValueError(f"run {run.pk} is marked RUNNING; refusing to resume")
        return self._execute(run)

    # -- internals ----------------------------------------------------------

    def _execute(self, run: WorkflowRun) -> WorkflowRun:
        run.status = WorkflowRun.Status.RUNNING
        run.attempt += 1
        run.last_error = ""
        run.started_at = run.started_at or timezone.now()
        run.save()
        steps = run.steps_by_name()

        for name, handler in STEP_SEQUENCE:
            step = steps[name]
            if step.status == WorkflowStepRun.Status.COMPLETED:
                logger.info("run=%s step=%s already completed, skipping", run.pk, name)
                continue
            run.current_step = name
            run.save(update_fields=["current_step", "updated_at"])
            try:
                result = self._run_step_with_retries(run, step, handler)
            except (TransientServiceError, PermanentServiceError) as exc:
                run.status = WorkflowRun.Status.FAILED
                run.last_error = f"{step.name}: {exc}"
                run.save()
                raise WorkflowFailed(run, step, exc) from exc
            run.context = {**run.context, **result}
            run.save(update_fields=["context", "updated_at"])

        run.status = WorkflowRun.Status.COMPLETED
        run.current_step = ""
        run.finished_at = timezone.now()
        run.save()
        logger.info("run=%s completed", run.pk)
        return run

    def _run_step_with_retries(
        self,
        run: WorkflowRun,
        step: WorkflowStepRun,
        handler: Callable[[WorkflowRun, WorkflowStepRun, ExternalSystems], dict[str, Any]],
    ) -> dict[str, Any]:
        step.status = WorkflowStepRun.Status.RUNNING
        step.started_at = step.started_at or timezone.now()
        step.save()
        for local_attempt in range(1, self.max_attempts + 1):
            step.attempts += 1
            step.save(update_fields=["attempts"])
            try:
                result = handler(run, step, self.systems)
            except TransientServiceError as exc:
                step.last_error = str(exc)
                step.save(update_fields=["last_error"])
                if local_attempt >= self.max_attempts:
                    step.status = WorkflowStepRun.Status.FAILED
                    step.save()
                    logger.error(
                        "run=%s step=%s failed after %s attempts: %s",
                        run.pk, step.name, step.attempts, exc,
                    )
                    raise
                delay = BACKOFF_SECONDS[min(local_attempt - 1, len(BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "run=%s step=%s transient failure (attempt %s): %s; retrying in %ss",
                    run.pk, step.name, step.attempts, exc, delay,
                )
                self.sleep(delay)
            except PermanentServiceError as exc:
                step.status = WorkflowStepRun.Status.FAILED
                step.last_error = str(exc)
                step.save()
                logger.error("run=%s step=%s permanent failure: %s", run.pk, step.name, exc)
                raise
            else:
                step.status = WorkflowStepRun.Status.COMPLETED
                step.result = result
                step.last_error = ""
                step.finished_at = timezone.now()
                step.save()
                logger.info("run=%s step=%s completed", run.pk, step.name)
                return result
        raise AssertionError("unreachable")
