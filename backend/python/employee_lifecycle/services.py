"""Entry points used by the HTTP API and management commands."""

import asyncio
from functools import lru_cache

from django.conf import settings

from .external_systems import ExternalSystems
from .models import RoleChangeRequest, WorkflowRun
from .orchestrator import RoleChangeOrchestrator, WorkflowFailed


@lru_cache(maxsize=1)
def get_external_systems() -> ExternalSystems:
    """Process-wide simulated downstream systems (stand-in for real clients)."""
    return ExternalSystems.simulated()


def _temporal_engine() -> bool:
    return getattr(settings, "EMPLOYEE_LIFECYCLE_ENGINE", "legacy") == "temporal"


async def connect_client():
    """Temporal client factory; tests patch this to inject a test client."""
    from .temporal.worker import connect

    return await connect(settings)


def start_role_change(request: RoleChangeRequest) -> WorkflowRun:
    if _temporal_engine():
        return start_role_change_temporal(request)
    return start_role_change_legacy(request)


def start_role_change_legacy(request: RoleChangeRequest) -> WorkflowRun:
    return RoleChangeOrchestrator(get_external_systems()).start(request)


def start_role_change_temporal(request: RoleChangeRequest) -> WorkflowRun:
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ApplicationError

    from .temporal.contracts import WORKFLOW_FAILED_ERROR
    from .temporal.worker import execute_role_change

    async def _execute():
        client = await connect_client()
        return await execute_role_change(
            client, request.pk, settings.TEMPORAL_TASK_QUEUE
        )

    try:
        result = asyncio.run(_execute())
    except WorkflowFailureError as exc:
        run = WorkflowRun.objects.get(request=request)
        cause = exc.cause
        if isinstance(cause, ApplicationError) and cause.type == WORKFLOW_FAILED_ERROR:
            step_name = (run.last_error or "").split(":", 1)[0]
            step = run.steps.filter(name=step_name).first() or run.steps.first()
            raise WorkflowFailed(run, step, cause) from exc
        raise
    return WorkflowRun.objects.get(pk=result.run_id)


def resume_role_change(run: WorkflowRun) -> WorkflowRun:
    if _temporal_engine():
        if run.status == WorkflowRun.Status.COMPLETED:
            return run
        if run.status == WorkflowRun.Status.RUNNING:
            raise ValueError(f"run {run.pk} is marked RUNNING; refusing to resume")
        return start_role_change_temporal(run.request)
    return RoleChangeOrchestrator(get_external_systems()).resume(run)
