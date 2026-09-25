"""Run a Temporal worker for the employee lifecycle task queue."""

import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand

from employee_lifecycle.services import get_external_systems
from employee_lifecycle.temporal.worker import build_worker, connect


class Command(BaseCommand):
    help = "Run a Temporal worker serving the employee lifecycle task queue"

    def add_arguments(self, parser):
        parser.add_argument(
            "--task-queue",
            default=None,
            help="Task queue to poll (default: TEMPORAL_TASK_QUEUE setting)",
        )

    def handle(self, *args, task_queue=None, **options):
        task_queue = task_queue or settings.TEMPORAL_TASK_QUEUE
        asyncio.run(self._run(task_queue))

    async def _run(self, task_queue: str) -> None:
        client = await connect(settings)
        worker = build_worker(client, get_external_systems(), task_queue=task_queue)
        self.stdout.write(f"Temporal worker polling task queue {task_queue!r}")
        await worker.run()
