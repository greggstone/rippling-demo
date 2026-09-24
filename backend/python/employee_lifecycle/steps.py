"""Individual steps of the role/location change workflow.

Each step receives the ``WorkflowRun``, the ``WorkflowStepRun`` row that
tracks it and the external system clients, and returns a JSON-serialisable
result that the orchestrator stores on the step row and merges into the run
context. Steps read what they need from ``run.context``.
"""

import logging
from typing import Any

from django.db import transaction

from . import policies
from .external_systems import ExternalSystems
from .models import Employee, WorkflowRun, WorkflowStepRun

logger = logging.getLogger(__name__)

EMPLOYEE_FIELDS = (
    "job_title",
    "department",
    "level",
    "is_manager",
    "work_country",
    "work_state",
    "work_location",
    "is_remote",
    "annual_salary_cents",
)


def _employee_snapshot(employee: Employee) -> dict[str, Any]:
    return {name: getattr(employee, name) for name in EMPLOYEE_FIELDS}


def _current_employee(run: WorkflowRun) -> Employee:
    """Always re-read the record; earlier steps may have changed it."""
    return Employee.objects.get(pk=run.request.employee_id)


def update_hr_record(
    run: WorkflowRun, step: WorkflowStepRun, systems: ExternalSystems
) -> dict[str, Any]:
    """Apply the requested change to the core employee record."""
    request = run.request
    with transaction.atomic():
        employee = Employee.objects.select_for_update().get(pk=request.employee_id)
        if not run.previous_state:
            run.previous_state = _employee_snapshot(employee)
            run.save(update_fields=["previous_state"])
        for name in EMPLOYEE_FIELDS:
            setattr(employee, name, getattr(request, f"new_{name}"))
        employee.save()
    logger.info("run=%s hr record updated for %s", run.pk, employee.employee_number)
    return {"employee": _employee_snapshot(employee)}


def recalculate_eligibility(
    run: WorkflowRun, step: WorkflowStepRun, systems: ExternalSystems
) -> dict[str, Any]:
    """Recompute benefits/policy eligibility and push enrollment changes."""
    employee = _current_employee(run)
    plans = policies.benefits_plans(employee)
    assignments = policies.policy_assignments(employee)
    confirmation = systems.benefits.update_enrollment(
        employee.employee_number, plans, idempotency_key=step.idempotency_key
    )
    return {
        "plans": plans,
        "policies": assignments,
        "benefits_confirmation_id": confirmation["confirmation_id"],
    }


def update_payroll(
    run: WorkflowRun, step: WorkflowStepRun, systems: ExternalSystems
) -> dict[str, Any]:
    """Send the new pay group / tax jurisdiction / salary to payroll."""
    employee = _current_employee(run)
    config = policies.payroll_configuration(employee)
    confirmation = systems.payroll.update_configuration(
        employee.employee_number, config, idempotency_key=step.idempotency_key
    )
    if confirmation.get("duplicate"):
        logger.warning(
            "run=%s payroll change %s was already applied; reusing confirmation",
            run.pk,
            step.idempotency_key,
        )
    return {
        "payroll_config": config,
        "payroll_confirmation_id": confirmation["confirmation_id"],
    }


def reconcile_access(
    run: WorkflowRun, step: WorkflowStepRun, systems: ExternalSystems
) -> dict[str, Any]:
    """Grant/revoke identity groups so they match the new role.

    Each grant and revoke is a separate remote call. The step re-reads the
    current memberships every time it runs so a retry after a partial
    application only issues the calls that are still needed.
    """
    employee = _current_employee(run)
    desired = policies.required_access_groups(employee)
    current = systems.identity.list_groups(employee.employee_number)
    granted: list[str] = []
    revoked: list[str] = []
    for group in sorted(desired - current):
        systems.identity.grant(employee.employee_number, group)
        granted.append(group)
    for group in sorted(current - desired):
        systems.identity.revoke(employee.employee_number, group)
        revoked.append(group)
    return {"access_groups": sorted(desired), "granted": granted, "revoked": revoked}


def notify_downstream(
    run: WorkflowRun, step: WorkflowStepRun, systems: ExternalSystems
) -> dict[str, Any]:
    """Publish the change event for reporting, facilities, IT ticketing, etc."""
    employee = _current_employee(run)
    context = run.context
    event_id = step.idempotency_key
    systems.notifications.publish(
        topic="employee.role_location_changed",
        event_id=event_id,
        payload={
            "employee_number": employee.employee_number,
            "effective_date": run.request.effective_date.isoformat(),
            "previous": run.previous_state,
            "current": context["employee"],
            "benefits_confirmation_id": context["benefits_confirmation_id"],
            "payroll_confirmation_id": context["payroll_confirmation_id"],
            "access_groups": context["access_groups"],
        },
    )
    return {"event_id": event_id}


# Execution order. Later steps assume everything before them has completed.
STEP_SEQUENCE = [
    ("update_hr_record", update_hr_record),
    ("recalculate_eligibility", recalculate_eligibility),
    ("update_payroll", update_payroll),
    ("reconcile_access", reconcile_access),
    ("notify_downstream", notify_downstream),
]
