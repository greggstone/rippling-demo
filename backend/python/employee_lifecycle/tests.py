"""Characterization tests for the legacy role/location change workflow.

These pin down the current behaviour of the sequential orchestrator so it can
be modernised safely later. External systems are simulated in-memory and
failures are injected deterministically through ``FaultPlan``.
"""

import json
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase

from . import services
from .external_systems import ExternalSystems, FaultPlan
from .models import Employee, RoleChangeRequest, WorkflowRun, WorkflowStepRun
from .orchestrator import RoleChangeOrchestrator, WorkflowFailed
from .steps import STEP_SEQUENCE

STEP_NAMES = [name for name, _ in STEP_SEQUENCE]


def make_employee() -> Employee:
    return Employee.objects.create(
        employee_number="E1001",
        first_name="Priya",
        last_name="Natarajan",
        email="priya@example.test",
        job_title="Software Engineer II",
        department="Engineering",
        level=3,
        is_manager=False,
        work_country="US",
        work_state="TX",
        work_location="Austin",
        is_remote=False,
        annual_salary_cents=15_000_000,
    )


def make_request(employee: Employee) -> RoleChangeRequest:
    return RoleChangeRequest.objects.create(
        employee=employee,
        effective_date=date(2026, 10, 1),
        requested_by="hrbp@example.test",
        new_job_title="Engineering Manager",
        new_department="Engineering",
        new_level=5,
        new_is_manager=True,
        new_work_country="US",
        new_work_state="CA",
        new_work_location="San Francisco",
        new_is_remote=True,
        new_annual_salary_cents=21_000_000,
    )


class WorkflowTestCase(TestCase):
    def setUp(self):
        self.employee = make_employee()
        self.request = make_request(self.employee)
        self.sleeps: list[float] = []

    def orchestrator(self, faults: FaultPlan | None = None) -> RoleChangeOrchestrator:
        self.systems = ExternalSystems.simulated(faults)
        # Legacy state the employee already has in the IdP before the change.
        self.systems.identity.memberships["E1001"] = {
            "all-employees",
            "dept-engineering",
            "region-us",
            "source-control",
            "office-austin-badge",
        }
        return RoleChangeOrchestrator(self.systems, sleep=self.sleeps.append)

    def step_statuses(self, run: WorkflowRun) -> dict[str, str]:
        return {s.name: s.status for s in run.steps.all()}


class SuccessfulWorkflowTests(WorkflowTestCase):
    def test_all_steps_complete_in_order_and_systems_are_updated(self):
        run = self.orchestrator().start(self.request)

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        self.assertEqual(run.attempt, 1)
        self.assertEqual(run.current_step, "")
        self.assertIsNotNone(run.finished_at)
        self.assertEqual([s.name for s in run.steps.all()], STEP_NAMES)
        self.assertEqual(
            self.step_statuses(run),
            {name: WorkflowStepRun.Status.COMPLETED for name in STEP_NAMES},
        )
        self.assertTrue(all(s.attempts == 1 for s in run.steps.all()))
        self.assertEqual(self.sleeps, [])

        # HR record
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.job_title, "Engineering Manager")
        self.assertEqual(self.employee.work_state, "CA")
        self.assertTrue(self.employee.is_manager)
        self.assertEqual(run.previous_state["job_title"], "Software Engineer II")
        self.assertEqual(run.previous_state["work_state"], "TX")

        # Benefits
        self.assertEqual(
            self.systems.benefits.enrollments["E1001"]["plans"],
            sorted([
                "medical_ppo", "dental_basic", "vision_basic", "401k_match",
                "ca_sdi", "executive_life", "remote_stipend",
            ]),
        )
        self.assertEqual(run.context["policies"]["pto"], "unlimited")

        # Payroll
        payroll = self.systems.payroll.configurations["E1001"]
        self.assertEqual(payroll["tax_jurisdiction"], "CA-PIT")
        self.assertEqual(payroll["pay_group"], "US-SEMIMONTHLY")
        self.assertEqual(payroll["annual_salary_cents"], 21_000_000)

        # Access
        self.assertEqual(
            self.systems.identity.memberships["E1001"],
            {
                "all-employees", "dept-engineering", "region-us",
                "source-control", "people-managers", "leadership",
            },
        )
        self.assertEqual(run.context["granted"], ["leadership", "people-managers"])
        self.assertEqual(run.context["revoked"], ["office-austin-badge"])

        # Notification
        self.assertEqual(len(self.systems.notifications.published), 1)
        event = self.systems.notifications.published[0]
        self.assertEqual(event["topic"], "employee.role_location_changed")
        self.assertEqual(event["payload"]["previous"]["work_location"], "Austin")
        self.assertEqual(event["payload"]["current"]["work_location"], "San Francisco")
        self.assertEqual(event["payload"]["payroll_confirmation_id"], "PAY-E1001-1")

    def test_resume_of_completed_run_is_a_noop(self):
        orchestrator = self.orchestrator()
        run = orchestrator.start(self.request)
        calls_before = len(self.systems.payroll.calls)
        orchestrator.resume(run)
        self.assertEqual(len(self.systems.payroll.calls), calls_before)


class TransientFailureTests(WorkflowTestCase):
    def test_transient_payroll_failure_is_retried_and_workflow_completes(self):
        faults = FaultPlan(transient={"payroll.update_configuration": 2})
        run = self.orchestrator(faults).start(self.request)

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        payroll_step = run.steps.get(name="update_payroll")
        self.assertEqual(payroll_step.status, WorkflowStepRun.Status.COMPLETED)
        self.assertEqual(payroll_step.attempts, 3)
        self.assertEqual(payroll_step.last_error, "")
        self.assertEqual(self.systems.payroll.call_count("update_configuration"), 3)
        self.assertEqual(self.sleeps, [0.5, 2.0])
        # The provider only applied the change once.
        self.assertEqual(run.context["payroll_confirmation_id"], "PAY-E1001-1")
        self.assertEqual(run.attempt, 1)

    def test_retry_budget_is_exhausted_after_max_attempts(self):
        faults = FaultPlan(transient={"benefits.update_enrollment": 3})
        with self.assertRaises(WorkflowFailed) as ctx:
            self.orchestrator(faults).start(self.request)

        run = ctx.exception.run
        self.assertEqual(run.status, WorkflowRun.Status.FAILED)
        self.assertEqual(run.current_step, "recalculate_eligibility")
        self.assertIn("upstream timeout", run.last_error)
        step = run.steps.get(name="recalculate_eligibility")
        self.assertEqual(step.status, WorkflowStepRun.Status.FAILED)
        self.assertEqual(step.attempts, 3)
        self.assertEqual(self.sleeps, [0.5, 2.0])


class PartialFailureTests(WorkflowTestCase):
    def test_permanent_failure_leaves_earlier_side_effects_applied(self):
        faults = FaultPlan(permanent={"identity.grant"})
        with self.assertRaises(WorkflowFailed) as ctx:
            self.orchestrator(faults).start(self.request)

        run = ctx.exception.run
        self.assertEqual(run.status, WorkflowRun.Status.FAILED)
        self.assertEqual(run.current_step, "reconcile_access")
        self.assertEqual(
            self.step_statuses(run),
            {
                "update_hr_record": WorkflowStepRun.Status.COMPLETED,
                "recalculate_eligibility": WorkflowStepRun.Status.COMPLETED,
                "update_payroll": WorkflowStepRun.Status.COMPLETED,
                "reconcile_access": WorkflowStepRun.Status.FAILED,
                "notify_downstream": WorkflowStepRun.Status.PENDING,
            },
        )
        # Permanent errors are not retried.
        self.assertEqual(run.steps.get(name="reconcile_access").attempts, 1)
        self.assertEqual(self.sleeps, [])

        # Everything before the failing step has already hit the outside world
        # and the HR record is already changed; nothing is rolled back.
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.work_state, "CA")
        self.assertIn("E1001", self.systems.benefits.enrollments)
        self.assertIn("E1001", self.systems.payroll.configurations)
        self.assertEqual(self.systems.notifications.published, [])
        self.assertNotIn("access_groups", run.context)

    def test_access_reconciliation_can_be_left_half_applied(self):
        # First grant succeeds, every grant after that times out.
        orchestrator = self.orchestrator()
        original_grant = self.systems.identity.grant

        def grant(employee_number, group):
            original_grant(employee_number, group)
            self.systems.identity.faults.transient["identity.grant"] = 99

        self.systems.identity.grant = grant

        with self.assertRaises(WorkflowFailed):
            orchestrator.start(self.request)

        # "leadership" was granted on the first pass; "people-managers" was not,
        # and the stale badge group was never revoked.
        self.assertIn("leadership", self.systems.identity.memberships["E1001"])
        self.assertNotIn("people-managers", self.systems.identity.memberships["E1001"])
        self.assertIn("office-austin-badge", self.systems.identity.memberships["E1001"])


class ResumeTests(WorkflowTestCase):
    def test_resume_skips_completed_steps_and_finishes(self):
        faults = FaultPlan(transient={"identity.list_groups": 3})
        orchestrator = self.orchestrator(faults)
        with self.assertRaises(WorkflowFailed) as ctx:
            orchestrator.start(self.request)
        run = ctx.exception.run
        self.assertEqual(run.current_step, "reconcile_access")

        benefits_calls = len(self.systems.benefits.calls)
        payroll_calls = len(self.systems.payroll.calls)
        self.employee.refresh_from_db()
        updated_at_before = self.employee.updated_at

        # Outage clears; operator resumes the run.
        self.sleeps.clear()
        run = orchestrator.resume(WorkflowRun.objects.get(pk=run.pk))

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        self.assertEqual(run.attempt, 2)
        self.assertEqual(
            self.step_statuses(run),
            {name: WorkflowStepRun.Status.COMPLETED for name in STEP_NAMES},
        )
        # Completed steps were not re-executed.
        self.assertEqual(len(self.systems.benefits.calls), benefits_calls)
        self.assertEqual(len(self.systems.payroll.calls), payroll_calls)
        self.assertEqual(run.steps.get(name="update_hr_record").attempts, 1)
        self.assertEqual(run.steps.get(name="update_payroll").attempts, 1)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.updated_at, updated_at_before)
        # The failed step was picked up again and the remainder ran.
        self.assertEqual(run.steps.get(name="reconcile_access").attempts, 4)
        self.assertEqual(run.steps.get(name="notify_downstream").attempts, 1)
        self.assertEqual(len(self.systems.notifications.published), 1)
        self.assertEqual(self.sleeps, [])

    def test_resume_after_partial_access_reconciliation_only_issues_missing_calls(self):
        faults = FaultPlan(permanent={"identity.revoke"})
        orchestrator = self.orchestrator(faults)
        with self.assertRaises(WorkflowFailed):
            orchestrator.start(self.request)
        # Grants happened, the revoke failed permanently.
        self.assertEqual(self.systems.identity.call_count("grant"), 2)

        self.systems.identity.faults.permanent.clear()
        run = orchestrator.resume(WorkflowRun.objects.get(pk=self.request.workflow_run.pk))

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        # Re-reading current membership meant no grant was repeated.
        self.assertEqual(self.systems.identity.call_count("grant"), 2)
        self.assertEqual(self.systems.identity.call_count("revoke"), 2)
        self.assertEqual(run.context["granted"], [])
        self.assertEqual(run.context["revoked"], ["office-austin-badge"])

    def test_resume_refuses_run_marked_running(self):
        orchestrator = self.orchestrator()
        run = WorkflowRun.objects.create(
            request=self.request, status=WorkflowRun.Status.RUNNING
        )
        with self.assertRaises(ValueError):
            orchestrator.resume(run)

    def test_management_command_resumes_failed_runs(self):
        faults = FaultPlan(transient={"notifications.publish": 3})
        systems = ExternalSystems.simulated(faults)
        with self.assertRaises(WorkflowFailed):
            RoleChangeOrchestrator(systems, sleep=self.sleeps.append).start(self.request)

        out = StringIO()
        with patch.object(services, "get_external_systems", return_value=systems):
            call_command("resume_failed_workflows", stdout=out)
        self.assertIn("completed", out.getvalue())
        self.assertEqual(
            WorkflowRun.objects.get(request=self.request).status,
            WorkflowRun.Status.COMPLETED,
        )


class ApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.employee = make_employee()
        services.get_external_systems.cache_clear()

    def tearDown(self):
        services.get_external_systems.cache_clear()

    def test_create_and_fetch_run(self):
        response = self.client.post(
            "/employee-lifecycle/role-changes/",
            data=json.dumps({
                "employee_number": "E1001",
                "effective_date": "2026-10-01",
                "job_title": "Staff Engineer",
                "department": "Engineering",
                "level": 6,
                "is_manager": False,
                "work_country": "GB",
                "work_state": "",
                "work_location": "London",
                "is_remote": False,
                "annual_salary_cents": 14_000_000,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["status"], "COMPLETED")
        self.assertEqual(body["context"]["payroll_config"]["pay_group"], "GB-MONTHLY")
        self.assertIn("intl_pension", body["context"]["plans"])

        detail = self.client.get(f"/employee-lifecycle/runs/{body['id']}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["steps"]), len(STEP_NAMES))

    def test_unknown_employee_returns_404(self):
        response = self.client.post(
            "/employee-lifecycle/role-changes/",
            data=json.dumps({"employee_number": "nope"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
