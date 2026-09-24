"""Minimal JSON API for submitting role changes and inspecting workflow runs."""

import json
from datetime import date
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Employee, RoleChangeRequest, WorkflowRun
from .orchestrator import WorkflowFailed
from .services import resume_role_change, start_role_change

REQUEST_FIELDS = (
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


def _serialize_run(run: WorkflowRun) -> dict[str, Any]:
    return {
        "id": run.pk,
        "request_id": run.request_id,
        "status": run.status,
        "current_step": run.current_step,
        "attempt": run.attempt,
        "last_error": run.last_error,
        "context": run.context,
        "steps": [
            {
                "name": step.name,
                "status": step.status,
                "attempts": step.attempts,
                "last_error": step.last_error,
                "result": step.result,
            }
            for step in run.steps.all()
        ],
    }


@csrf_exempt
@require_http_methods(["POST"])
def create_role_change(request: HttpRequest) -> HttpResponse:
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    try:
        employee = Employee.objects.get(employee_number=body["employee_number"])
    except (KeyError, Employee.DoesNotExist):
        return JsonResponse({"error": "unknown employee"}, status=404)

    missing = [name for name in REQUEST_FIELDS if name not in body]
    if missing:
        return JsonResponse({"error": f"missing fields: {', '.join(missing)}"}, status=400)

    change = RoleChangeRequest.objects.create(
        employee=employee,
        effective_date=date.fromisoformat(body.get("effective_date", date.today().isoformat())),
        requested_by=body.get("requested_by", "api"),
        **{f"new_{name}": body[name] for name in REQUEST_FIELDS},
    )
    try:
        run = start_role_change(change)
    except WorkflowFailed as exc:
        return JsonResponse(_serialize_run(exc.run), status=502)
    return JsonResponse(_serialize_run(run), status=201)


@require_http_methods(["GET"])
def run_detail(request: HttpRequest, run_id: int) -> HttpResponse:
    try:
        run = WorkflowRun.objects.get(pk=run_id)
    except WorkflowRun.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse(_serialize_run(run))


@csrf_exempt
@require_http_methods(["POST"])
def resume_run(request: HttpRequest, run_id: int) -> HttpResponse:
    try:
        run = WorkflowRun.objects.get(pk=run_id)
    except WorkflowRun.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    try:
        run = resume_role_change(run)
    except WorkflowFailed as exc:
        return JsonResponse(_serialize_run(exc.run), status=502)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse(_serialize_run(run))
