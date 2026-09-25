"""Data contracts shared between the workflow, activities and callers.

Pure dataclasses and constants only -- nothing in here may import Django so
the workflow sandbox can load it safely.
"""

from dataclasses import dataclass
from typing import Any

TASK_QUEUE_DEFAULT = "employee-lifecycle"

STEP_ORDER = (
    "update_hr_record",
    "recalculate_eligibility",
    "update_payroll",
    "reconcile_access",
    "notify_downstream",
)
# Steps that only depend on update_hr_record and can run concurrently.
PARALLEL_STEPS = (
    "recalculate_eligibility",
    "update_payroll",
    "reconcile_access",
)

MAX_ATTEMPTS = 3

PERMANENT_ERROR = "PermanentServiceError"
TRANSIENT_ERROR = "TransientServiceError"
WORKFLOW_FAILED_ERROR = "RoleChangeWorkflowFailed"


def workflow_id(request_id: int) -> str:
    return f"role-change-{request_id}"


@dataclass
class RoleChangeInput:
    request_id: int


@dataclass
class RunRef:
    run_id: int


@dataclass
class StepInput:
    run_id: int
    step: str
    context: dict[str, Any]


@dataclass
class StepOutput:
    step: str
    result: dict[str, Any]
    attempts: int
    # True when the activity short-circuited because the step row was
    # already COMPLETED (at-least-once re-execution safety).
    skipped: bool = False


@dataclass
class RunOutcome:
    run_id: int
    status: str
    last_error: str = ""


@dataclass
class RoleChangeResult:
    run_id: int
    context: dict[str, Any]


@dataclass
class WorkflowProgress:
    run_id: int | None
    current_steps: list[str]
    completed: list[str]
    failed: list[str]
    attempts: dict[str, int]
    status: str
