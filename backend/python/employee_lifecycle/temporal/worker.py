"""Worker and client helpers for the role/location change workflow."""

from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client, WorkflowFailureError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.worker import Worker

from ..external_systems import ExternalSystems
from . import contracts
from .activities import RoleChangeActivities
from .contracts import RoleChangeInput, RoleChangeResult
from .workflows import RoleChangeWorkflow

__all__ = ["WorkflowFailureError", "build_worker", "execute_role_change"]


def build_worker(
    client: Client,
    systems: ExternalSystems,
    task_queue: str = contracts.TASK_QUEUE,
    max_concurrent_activities: int = 8,
) -> Worker:
    """Activities are synchronous (Django ORM), so they run in a thread pool."""
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[RoleChangeWorkflow],
        activities=RoleChangeActivities(systems).all(),
        activity_executor=ThreadPoolExecutor(max_workers=max_concurrent_activities),
    )


async def execute_role_change(
    client: Client, request_id: int, task_queue: str = contracts.TASK_QUEUE
) -> RoleChangeResult:
    """Start the workflow for ``request_id`` and wait for it to finish.

    The workflow id is derived from the request, so a request can never have
    two runs in flight. A run that previously failed may be started again;
    completed steps are skipped by the activities' idempotency guards.
    """
    return await client.execute_workflow(
        RoleChangeWorkflow.run,
        RoleChangeInput(request_id=request_id),
        id=contracts.workflow_id(request_id),
        task_queue=task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
    )
