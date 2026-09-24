"""Entry points used by the HTTP API and management commands."""

from functools import lru_cache

from django.conf import settings
from temporalio.client import Client

from .external_systems import ExternalSystems
from .models import RoleChangeRequest, WorkflowRun
from .orchestrator import RoleChangeOrchestrator
from .temporal.worker import execute_role_change


@lru_cache(maxsize=1)
def get_external_systems() -> ExternalSystems:
    """Process-wide simulated downstream systems (stand-in for real clients)."""
    return ExternalSystems.simulated()


def start_role_change(request: RoleChangeRequest) -> WorkflowRun:
    return RoleChangeOrchestrator(get_external_systems()).start(request)


def resume_role_change(run: WorkflowRun) -> WorkflowRun:
    return RoleChangeOrchestrator(get_external_systems()).resume(run)


async def start_role_change_durable(request: RoleChangeRequest) -> WorkflowRun:
    """Run the request through Temporal (requires a reachable Temporal service
    and a worker started with ``manage.py run_temporal_worker``)."""
    client = await Client.connect(settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE)
    result = await execute_role_change(client, request.pk)
    return await WorkflowRun.objects.aget(pk=result.run_id)
