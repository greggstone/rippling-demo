"""Types shared between the workflow, the activities and their callers.

This module is imported inside the workflow sandbox, so it must stay free of
Django (or any other non-deterministic) imports.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio.common import RetryPolicy

TASK_QUEUE = "employee-lifecycle"
WORKFLOW_NAME = "employee_role_location_change"

# Activity names. The workflow refers to activities by name so it never has
# to import the Django-backed implementation module.
CREATE_RUN = "create_run"
UPDATE_HR_RECORD = "update_hr_record"
RECALCULATE_ELIGIBILITY = "recalculate_eligibility"
UPDATE_PAYROLL = "update_payroll"
RECONCILE_ACCESS = "reconcile_access"
NOTIFY_DOWNSTREAM = "notify_downstream"
SAVE_CONTEXT = "save_context"
FINISH_RUN = "finish_run"

# Steps 2-4 only depend on the HR record having been updated, and each one
# talks to a different external system, so they are fanned out.
FAN_OUT_STEPS = (RECALCULATE_ELIGIBILITY, UPDATE_PAYROLL, RECONCILE_ACCESS)
STEP_ORDER = (UPDATE_HR_RECORD, *FAN_OUT_STEPS, NOTIFY_DOWNSTREAM)

# Error types raised by activities. ``PERMANENT_ERROR`` is listed as
# non-retryable so Temporal fails the activity on the first occurrence.
TRANSIENT_ERROR = "TransientServiceError"
PERMANENT_ERROR = "PermanentServiceError"
WORKFLOW_FAILED_ERROR = "RoleChangeWorkflowFailed"

# Mirrors the legacy schedule: 3 attempts, 0.5s then 2.0s between them
# (capped at 5.0s), no retry for permanent provider rejections.
REMOTE_STEP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=0.5),
    backoff_coefficient=4.0,
    maximum_interval=timedelta(seconds=5.0),
    maximum_attempts=3,
    non_retryable_error_types=[PERMANENT_ERROR],
)
# Local bookkeeping writes only ever fail on infrastructure trouble.
BOOKKEEPING_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=0.5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=10,
)

REMOTE_STEP_TIMEOUT = timedelta(seconds=30)
BOOKKEEPING_TIMEOUT = timedelta(seconds=10)


def workflow_id(request_id: int) -> str:
    return f"role-change-{request_id}"


def idempotency_key(request_id: int, step: str) -> str:
    return f"rcr-{request_id}-{step}"


@dataclass
class RoleChangeInput:
    request_id: int


@dataclass
class StepInput:
    run_id: int
    step: str


@dataclass
class SaveContextInput:
    run_id: int
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepFailure:
    step: str
    error_type: str
    message: str


@dataclass
class FinishRunInput:
    run_id: int
    status: str
    context: dict[str, Any] = field(default_factory=dict)
    failures: list[StepFailure] = field(default_factory=list)


@dataclass
class RoleChangeResult:
    run_id: int
    status: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowProgress:
    """Answer to the ``progress`` query."""

    run_id: int | None
    phase: str
    completed_steps: list[str] = field(default_factory=list)
    in_flight_steps: list[str] = field(default_factory=list)
    failures: list[StepFailure] = field(default_factory=list)
