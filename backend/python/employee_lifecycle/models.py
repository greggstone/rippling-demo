"""Persistence for the role/location change workflow.

The workflow engine keeps its own state in these tables. Every step writes a
row before it starts and updates it when it finishes so that an operator (or
the resume command) can work out where a run stopped.
"""

from django.db import models
from django.utils import timezone


class Employee(models.Model):
    employee_number = models.CharField(max_length=20, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    job_title = models.CharField(max_length=120)
    department = models.CharField(max_length=120)
    level = models.PositiveSmallIntegerField(default=1)
    is_manager = models.BooleanField(default=False)
    work_country = models.CharField(max_length=2)
    work_state = models.CharField(max_length=2, blank=True)
    work_location = models.CharField(max_length=120)
    is_remote = models.BooleanField(default=False)
    annual_salary_cents = models.BigIntegerField()
    pay_currency = models.CharField(max_length=3, default="USD")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.employee_number} {self.first_name} {self.last_name}"


class RoleChangeRequest(models.Model):
    """The business event that kicks off the workflow."""

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="role_changes"
    )
    effective_date = models.DateField()
    requested_by = models.CharField(max_length=120)
    new_job_title = models.CharField(max_length=120)
    new_department = models.CharField(max_length=120)
    new_level = models.PositiveSmallIntegerField()
    new_is_manager = models.BooleanField(default=False)
    new_work_country = models.CharField(max_length=2)
    new_work_state = models.CharField(max_length=2, blank=True)
    new_work_location = models.CharField(max_length=120)
    new_is_remote = models.BooleanField(default=False)
    new_annual_salary_cents = models.BigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"RoleChangeRequest #{self.pk} for {self.employee}"


class WorkflowRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        RUNNING = "RUNNING"
        FAILED = "FAILED"
        COMPLETED = "COMPLETED"

    WORKFLOW_TYPE = "employee_role_location_change"

    request = models.OneToOneField(
        RoleChangeRequest, on_delete=models.PROTECT, related_name="workflow_run"
    )
    workflow_type = models.CharField(max_length=80, default=WORKFLOW_TYPE)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    current_step = models.CharField(max_length=80, blank=True)
    attempt = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    # Snapshot of the employee record before any mutation, used by the
    # eligibility and payroll steps to compute deltas.
    previous_state = models.JSONField(default=dict)
    # Results published by completed steps for consumption by later steps.
    context = models.JSONField(default=dict)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"WorkflowRun #{self.pk} [{self.status}] {self.current_step}"

    def steps_by_name(self) -> dict[str, "WorkflowStepRun"]:
        return {step.name: step for step in self.steps.all()}


class WorkflowStepRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        RUNNING = "RUNNING"
        FAILED = "FAILED"
        COMPLETED = "COMPLETED"

    run = models.ForeignKey(
        WorkflowRun, on_delete=models.CASCADE, related_name="steps"
    )
    name = models.CharField(max_length=80)
    sequence = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    # Key sent to external systems so a retried call is not applied twice.
    idempotency_key = models.CharField(max_length=120)
    result = models.JSONField(default=dict)
    last_error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("run", "name")]
        ordering = ["sequence"]

    def __str__(self) -> str:
        return f"{self.run_id}:{self.name} [{self.status}]"
