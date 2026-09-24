"""Operator command: retry FAILED role change runs.

Typically run by hand (or from cron) after a downstream outage clears.
"""

from django.core.management.base import BaseCommand

from employee_lifecycle.models import WorkflowRun
from employee_lifecycle.orchestrator import WorkflowFailed
from employee_lifecycle.services import resume_role_change


class Command(BaseCommand):
    help = "Resume workflow runs that are in the FAILED state"

    def add_arguments(self, parser):
        parser.add_argument("--run-id", type=int, help="Resume only this run")

    def handle(self, *args, **options):
        runs = WorkflowRun.objects.filter(status=WorkflowRun.Status.FAILED)
        if options["run_id"]:
            runs = runs.filter(pk=options["run_id"])
        if not runs.exists():
            self.stdout.write("No failed runs to resume.")
            return
        for run in runs.order_by("pk"):
            self.stdout.write(f"Resuming run {run.pk} (stopped at {run.current_step})...")
            try:
                resume_role_change(run)
            except WorkflowFailed as exc:
                self.stderr.write(f"  run {run.pk} failed again at {exc.step.name}: {exc.cause}")
            else:
                self.stdout.write(self.style.SUCCESS(f"  run {run.pk} completed"))
