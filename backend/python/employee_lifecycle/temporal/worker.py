"""Worker construction and client helpers for the Temporal implementation."""

from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client, WorkflowHandle
from temporalio.worker import Worker
from temporalio.common import WorkflowIDReusePolicy

from ..external_systems import ExternalSystems
from .activities import RoleChangeActivities
from .contracts import (
    TASK_QUEUE_DEFAULT,
    RoleChangeInput,
    RoleChangeResult,
    workflow_id,
)
from .workflows import RoleChangeWorkflow


async def connect(settings) -> Client:
    return await Client.connect(
        settings.TEMPORAL_ADDRESS, namespace=settings.TEMPORAL_NAMESPACE
    )


def build_worker(
    client: Client,
    systems: ExternalSystems,
    task_queue: str = TASK_QUEUE_DEFAULT,
    *,
    max_workers: int = 8,
) -> Worker:
    activities = RoleChangeActivities(systems)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[RoleChangeWorkflow],
        activities=[
            activities.create_run,
            activities.run_step,
            activities.mark_run_failed,
            activities.mark_run_completed,
        ],
        # Activities are synchronous and use the Django ORM.
        activity_executor=ThreadPoolExecutor(max_workers=max_workers),
    )


async def start_role_change(
    client: Client, request_id: int, task_queue: str = TASK_QUEUE_DEFAULT
) -> WorkflowHandle:
    return await client.start_workflow(
        RoleChangeWorkflow.run,
        RoleChangeInput(request_id=request_id),
        id=workflow_id(request_id),
        task_queue=task_queue,
        # A re-run after failure reuses the same workflow id; a duplicate
        # start while one is running is rejected by the server.
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
    )


async def execute_role_change(
    client: Client, request_id: int, task_queue: str = TASK_QUEUE_DEFAULT
) -> RoleChangeResult:
    handle = await start_role_change(client, request_id, task_queue)
    return await handle.result()
