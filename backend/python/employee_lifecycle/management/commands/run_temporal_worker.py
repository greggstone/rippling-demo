"""Run a Temporal worker for the employee lifecycle task queue.

    ./manage.py run_temporal_worker --address localhost:7233

Requires a reachable Temporal service (e.g. ``temporal server start-dev``).
"""

import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand
from temporalio.client import Client

from employee_lifecycle.services import get_external_systems
from employee_lifecycle.temporal import contracts
from employee_lifecycle.temporal.worker import build_worker


class Command(BaseCommand):
    help = "Run a Temporal worker for the employee lifecycle workflow"

    def add_arguments(self, parser):
        parser.add_argument("--address", default=settings.TEMPORAL_ADDRESS)
        parser.add_argument("--namespace", default=settings.TEMPORAL_NAMESPACE)
        parser.add_argument("--task-queue", default=contracts.TASK_QUEUE)

    def handle(self, *args, **options):
        asyncio.run(self._serve(options["address"], options["namespace"], options["task_queue"]))

    async def _serve(self, address: str, namespace: str, task_queue: str) -> None:
        client = await Client.connect(address, namespace=namespace)
        worker = build_worker(client, get_external_systems(), task_queue=task_queue)
        self.stdout.write(f"Worker listening on {task_queue} via {address}")
        await worker.run()
