"""Entry points used by the HTTP API and management commands."""

from functools import lru_cache

from .external_systems import ExternalSystems
from .models import RoleChangeRequest, WorkflowRun
from .orchestrator import RoleChangeOrchestrator


@lru_cache(maxsize=1)
def get_external_systems() -> ExternalSystems:
    """Process-wide simulated downstream systems (stand-in for real clients)."""
    return ExternalSystems.simulated()


def start_role_change(request: RoleChangeRequest) -> WorkflowRun:
    return RoleChangeOrchestrator(get_external_systems()).start(request)


def resume_role_change(run: WorkflowRun) -> WorkflowRun:
    return RoleChangeOrchestrator(get_external_systems()).resume(run)
