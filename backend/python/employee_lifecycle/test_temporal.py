"""Tests for the Temporal implementation of the role/location change workflow.

They run against Temporal's time-skipping test server
(``WorkflowEnvironment.start_time_skipping``), so retry backoff is skipped
and no separately managed Temporal service is needed. Activities use the
Django ORM from worker threads, hence ``TransactionTestCase``.
"""

import asyncio
import threading
import uuid

from django.test import TransactionTestCase
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment

from .external_systems import ExternalSystems, FaultPlan
from .models import Employee, RoleChangeRequest, WorkflowRun, WorkflowStepRun
from .temporal import contracts
from .temporal.contracts import RoleChangeInput, WorkflowProgress
from .temporal.worker import build_worker, execute_role_change
from .temporal.workflows import RoleChangeWorkflow
from .tests import make_employee, make_request

STEP_NAMES = list(contracts.STEP_ORDER)
COMPLETED = WorkflowStepRun.Status.COMPLETED
FAILED = WorkflowStepRun.Status.FAILED


class TemporalWorkflowTestCase(TransactionTestCase):
    def setUp(self):
        self.employee: Employee = make_employee()
        self.request: RoleChangeRequest = make_request(self.employee)
        self.task_queue = f"employee-lifecycle-test-{uuid.uuid4()}"
        self.systems = self.make_systems()

    def make_systems(self, faults: FaultPlan | None = None) -> ExternalSystems:
        systems = ExternalSystems.simulated(faults)
        systems.identity.memberships["E1001"] = {
            "all-employees",
            "dept-engineering",
            "region-us",
            "source-control",
            "office-austin-badge",
        }
        return systems

    def execute(self) -> WorkflowRun:
        """Run the workflow to completion, or raise the ``ApplicationError`` it failed with."""

        async def go():
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with build_worker(env.client, self.systems, task_queue=self.task_queue):
                    return await execute_role_change(
                        env.client, self.request.pk, task_queue=self.task_queue
                    )

        try:
            result = asyncio.run(go())
        except WorkflowFailureError as exc:
            raise exc.cause from exc
        return WorkflowRun.objects.get(pk=result.run_id)

    def failed_run(self) -> tuple[WorkflowRun, ApplicationError]:
        with self.assertRaises(ApplicationError) as ctx:
            self.execute()
        self.assertEqual(ctx.exception.type, contracts.WORKFLOW_FAILED_ERROR)
        return WorkflowRun.objects.get(request=self.request), ctx.exception

    def step_statuses(self, run: WorkflowRun) -> dict[str, str]:
        return {s.name: s.status for s in run.steps.all()}

    def attempts(self, run: WorkflowRun, name: str) -> int:
        return run.steps.get(name=name).attempts


class HappyPathTests(TemporalWorkflowTestCase):
    def test_workflow_completes_and_all_systems_are_updated(self):
        run = self.execute()

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        self.assertEqual(run.attempt, 1)
        self.assertEqual(run.current_step, "")
        self.assertEqual(run.last_error, "")
        self.assertIsNotNone(run.finished_at)
        self.assertEqual([s.name for s in run.steps.all()], STEP_NAMES)
        self.assertEqual(self.step_statuses(run), {n: COMPLETED for n in STEP_NAMES})
        self.assertTrue(all(s.attempts == 1 for s in run.steps.all()))

        self.employee.refresh_from_db()
        self.assertEqual(self.employee.job_title, "Engineering Manager")
        self.assertEqual(self.employee.work_state, "CA")
        self.assertTrue(self.employee.is_manager)
        self.assertEqual(run.previous_state["job_title"], "Software Engineer II")
        self.assertEqual(run.previous_state["work_state"], "TX")

        self.assertEqual(
            self.systems.benefits.enrollments["E1001"]["plans"],
            sorted([
                "medical_ppo", "dental_basic", "vision_basic", "401k_match",
                "ca_sdi", "executive_life", "remote_stipend",
            ]),
        )
        self.assertEqual(run.context["policies"]["pto"], "unlimited")

        payroll = self.systems.payroll.configurations["E1001"]
        self.assertEqual(payroll["tax_jurisdiction"], "CA-PIT")
        self.assertEqual(payroll["pay_group"], "US-SEMIMONTHLY")
        self.assertEqual(payroll["annual_salary_cents"], 21_000_000)
        self.assertEqual(self.systems.payroll.call_count("update_configuration"), 1)

        self.assertEqual(
            self.systems.identity.memberships["E1001"],
            {
                "all-employees", "dept-engineering", "region-us",
                "source-control", "people-managers", "leadership",
            },
        )
        self.assertEqual(run.context["granted"], ["leadership", "people-managers"])
        self.assertEqual(run.context["revoked"], ["office-austin-badge"])

        self.assertEqual(len(self.systems.notifications.published), 1)
        event = self.systems.notifications.published[0]
        self.assertEqual(event["topic"], "employee.role_location_changed")
        self.assertEqual(event["event_id"], f"rcr-{self.request.pk}-notify_downstream")
        self.assertEqual(event["payload"]["previous"]["work_location"], "Austin")
        self.assertEqual(event["payload"]["current"]["work_location"], "San Francisco")
        self.assertEqual(event["payload"]["payroll_confirmation_id"], "PAY-E1001-1")
        self.assertEqual(
            event["payload"]["benefits_confirmation_id"],
            run.context["benefits_confirmation_id"],
        )

    def test_progress_query_reports_completed_steps(self):
        async def go():
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with build_worker(env.client, self.systems, task_queue=self.task_queue):
                    handle: WorkflowHandle = await env.client.start_workflow(
                        RoleChangeWorkflow.run,
                        RoleChangeInput(request_id=self.request.pk),
                        id=contracts.workflow_id(self.request.pk),
                        task_queue=self.task_queue,
                    )
                    await handle.result()
                    return await handle.query(RoleChangeWorkflow.progress)

        progress: WorkflowProgress = asyncio.run(go())
        self.assertEqual(progress.phase, "completed")
        self.assertEqual(sorted(progress.completed_steps), sorted(STEP_NAMES))
        self.assertEqual(progress.in_flight_steps, [])
        self.assertEqual(progress.failures, [])
        self.assertEqual(progress.run_id, WorkflowRun.objects.get(request=self.request).pk)


class TransientFailureTests(TemporalWorkflowTestCase):
    def test_transient_payroll_failure_is_retried_by_temporal_and_applied_once(self):
        self.systems = self.make_systems(FaultPlan(transient={"payroll.update_configuration": 2}))
        run = self.execute()

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        payroll_step = run.steps.get(name="update_payroll")
        self.assertEqual(payroll_step.status, COMPLETED)
        self.assertEqual(payroll_step.attempts, 3)
        self.assertEqual(payroll_step.last_error, "")
        self.assertEqual(self.systems.payroll.call_count("update_configuration"), 3)
        # The provider only applied the change once (same idempotency key).
        self.assertEqual(run.context["payroll_confirmation_id"], "PAY-E1001-1")
        self.assertEqual(len(self.systems.payroll.configurations), 1)
        # Siblings were unaffected by the retries.
        self.assertEqual(self.attempts(run, "recalculate_eligibility"), 1)
        self.assertEqual(self.attempts(run, "reconcile_access"), 1)
        self.assertEqual(run.attempt, 1)

    def test_retry_budget_is_exhausted_after_max_attempts(self):
        self.systems = self.make_systems(FaultPlan(transient={"benefits.update_enrollment": 3}))
        run, error = self.failed_run()

        self.assertEqual(run.status, WorkflowRun.Status.FAILED)
        self.assertEqual(run.current_step, "recalculate_eligibility")
        self.assertIn("upstream timeout", run.last_error)
        step = run.steps.get(name="recalculate_eligibility")
        self.assertEqual(step.status, FAILED)
        self.assertEqual(step.attempts, contracts.REMOTE_STEP_RETRY.maximum_attempts)
        self.assertEqual(self.systems.benefits.call_count("update_enrollment"), 3)
        self.assertEqual(
            [(f["step"], f["error_type"]) for f in error.details],
            [("recalculate_eligibility", contracts.TRANSIENT_ERROR)],
        )
        # notify_downstream never ran; the independent siblings did finish.
        self.assertEqual(self.systems.notifications.published, [])
        step_status = self.step_statuses(run)
        self.assertEqual(step_status["notify_downstream"], WorkflowStepRun.Status.PENDING)
        self.assertEqual(step_status["update_payroll"], COMPLETED)
        self.assertEqual(step_status["reconcile_access"], COMPLETED)


class PartialFailureTests(TemporalWorkflowTestCase):
    def test_permanent_failure_is_not_retried_and_earlier_side_effects_stay_applied(self):
        self.systems = self.make_systems(FaultPlan(permanent={"identity.grant"}))
        run, error = self.failed_run()

        self.assertEqual(run.status, WorkflowRun.Status.FAILED)
        self.assertEqual(run.current_step, "reconcile_access")
        self.assertIn("request rejected by provider", run.last_error)
        self.assertEqual(
            self.step_statuses(run),
            {
                "update_hr_record": COMPLETED,
                "recalculate_eligibility": COMPLETED,
                "update_payroll": COMPLETED,
                "reconcile_access": FAILED,
                "notify_downstream": WorkflowStepRun.Status.PENDING,
            },
        )
        # Permanent errors are not retried.
        self.assertEqual(self.attempts(run, "reconcile_access"), 1)
        self.assertEqual(self.systems.identity.call_count("grant"), 1)
        self.assertEqual(error.details[0]["error_type"], contracts.PERMANENT_ERROR)

        # Nothing is rolled back and nothing downstream is published.
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.work_state, "CA")
        self.assertIn("E1001", self.systems.benefits.enrollments)
        self.assertIn("E1001", self.systems.payroll.configurations)
        self.assertEqual(self.systems.notifications.published, [])
        self.assertNotIn("access_groups", run.context)
        self.assertIn("payroll_confirmation_id", run.context)

    def test_failing_branch_does_not_stop_its_siblings(self):
        self.systems = self.make_systems(
            FaultPlan(permanent={"payroll.update_configuration", "benefits.update_enrollment"})
        )
        run, error = self.failed_run()

        self.assertEqual(
            self.step_statuses(run),
            {
                "update_hr_record": COMPLETED,
                "recalculate_eligibility": FAILED,
                "update_payroll": FAILED,
                "reconcile_access": COMPLETED,
                "notify_downstream": WorkflowStepRun.Status.PENDING,
            },
        )
        self.assertEqual(
            sorted(f["step"] for f in error.details), ["recalculate_eligibility", "update_payroll"]
        )
        self.assertIn("people-managers", self.systems.identity.memberships["E1001"])

    def test_half_applied_access_reconciliation_is_completed_by_retry(self):
        original_grant = self.systems.identity.grant

        def grant(employee_number, group):
            original_grant(employee_number, group)
            self.systems.identity.faults.transient["identity.grant"] = 1

        self.systems.identity.grant = grant
        run = self.execute()

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        # Attempt 1 granted "leadership" then timed out on "people-managers";
        # attempt 2 re-read the IdP state and only issued what was missing.
        self.assertEqual(self.attempts(run, "reconcile_access"), 2)
        self.assertEqual(self.systems.identity.call_count("grant"), 3)
        self.assertEqual(self.systems.identity.call_count("revoke"), 1)
        self.assertEqual(
            self.systems.identity.memberships["E1001"],
            {
                "all-employees", "dept-engineering", "region-us",
                "source-control", "people-managers", "leadership",
            },
        )
        self.assertEqual(run.context["granted"], ["people-managers"])


class RecoveryTests(TemporalWorkflowTestCase):
    def test_rerun_after_failure_skips_completed_steps_without_duplicate_side_effects(self):
        self.systems = self.make_systems(FaultPlan(transient={"identity.list_groups": 3}))
        run, _ = self.failed_run()
        self.assertEqual(run.current_step, "reconcile_access")
        self.assertEqual(self.attempts(run, "reconcile_access"), 3)

        benefits_calls = len(self.systems.benefits.calls)
        payroll_calls = len(self.systems.payroll.calls)
        self.employee.refresh_from_db()
        updated_at_before = self.employee.updated_at

        # Outage clears; the workflow is started again for the same request.
        run = self.execute()

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        self.assertEqual(run.attempt, 2)
        self.assertEqual(run.last_error, "")
        self.assertEqual(self.step_statuses(run), {n: COMPLETED for n in STEP_NAMES})
        # Completed steps were not re-executed.
        self.assertEqual(len(self.systems.benefits.calls), benefits_calls)
        self.assertEqual(len(self.systems.payroll.calls), payroll_calls)
        self.assertEqual(self.attempts(run, "update_hr_record"), 1)
        self.assertEqual(self.attempts(run, "update_payroll"), 1)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.updated_at, updated_at_before)
        # The failed step was picked up again and the remainder ran once.
        self.assertEqual(self.attempts(run, "reconcile_access"), 4)
        self.assertEqual(self.attempts(run, "notify_downstream"), 1)
        self.assertEqual(len(self.systems.notifications.published), 1)
        # Context was rebuilt from the stored step results.
        self.assertEqual(run.context["payroll_confirmation_id"], "PAY-E1001-1")
        self.assertEqual(WorkflowRun.objects.filter(request=self.request).count(), 1)

    def test_rerun_after_partial_access_reconciliation_only_issues_missing_calls(self):
        self.systems = self.make_systems(FaultPlan(permanent={"identity.revoke"}))
        self.failed_run()
        self.assertEqual(self.systems.identity.call_count("grant"), 2)

        self.systems.identity.faults.permanent.clear()
        run = self.execute()

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        self.assertEqual(self.systems.identity.call_count("grant"), 2)
        self.assertEqual(self.systems.identity.call_count("revoke"), 2)
        self.assertEqual(run.context["granted"], [])
        self.assertEqual(run.context["revoked"], ["office-austin-badge"])

    def test_rerun_of_completed_workflow_is_rejected_by_id_reuse_policy(self):
        async def go():
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with build_worker(env.client, self.systems, task_queue=self.task_queue):
                    await execute_role_change(env.client, self.request.pk, task_queue=self.task_queue)
                    with self.assertRaises(WorkflowAlreadyStartedError):
                        await execute_role_change(
                            env.client, self.request.pk, task_queue=self.task_queue
                        )

        asyncio.run(go())
        self.assertEqual(self.systems.payroll.call_count("update_configuration"), 1)
        self.assertEqual(len(self.systems.notifications.published), 1)


class ConcurrencyTests(TemporalWorkflowTestCase):
    def test_independent_steps_run_concurrently(self):
        """Benefits blocks until payroll has been called. Serial execution
        (benefits before payroll) would never get there."""
        payroll_called = threading.Event()
        original_benefits = self.systems.benefits.update_enrollment
        original_payroll = self.systems.payroll.update_configuration
        overlap = {"seen": False}

        def update_enrollment(*args, **kwargs):
            overlap["seen"] = payroll_called.wait(timeout=5)
            return original_benefits(*args, **kwargs)

        def update_configuration(*args, **kwargs):
            payroll_called.set()
            return original_payroll(*args, **kwargs)

        self.systems.benefits.update_enrollment = update_enrollment
        self.systems.payroll.update_configuration = update_configuration

        run = self.execute()

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        self.assertTrue(overlap["seen"], "benefits and payroll steps did not overlap")
        self.assertTrue(all(s.attempts == 1 for s in run.steps.all()))

    def test_hr_update_precedes_fan_out_and_notification_follows_it(self):
        order: list[str] = []
        lock = threading.Lock()

        def record(system, operation):
            original = getattr(system, operation)

            def wrapped(*args, **kwargs):
                with lock:
                    order.append(f"{system.name}.{operation}")
                return original(*args, **kwargs)

            setattr(system, operation, wrapped)

        record(self.systems.benefits, "update_enrollment")
        record(self.systems.payroll, "update_configuration")
        record(self.systems.identity, "list_groups")
        record(self.systems.notifications, "publish")

        run = self.execute()

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        self.assertEqual(order[-1], "notifications.publish")
        self.assertEqual(
            sorted(order[:-1]),
            ["benefits.update_enrollment", "identity.list_groups", "payroll.update_configuration"],
        )
        # Every fan-out step saw the already-updated HR record.
        self.assertEqual(run.context["payroll_config"]["tax_jurisdiction"], "CA-PIT")
        self.assertIn("ca_sdi", run.context["plans"])
        self.assertIn("leadership", run.context["access_groups"])
