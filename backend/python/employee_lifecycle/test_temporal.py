"""Tests for the Temporal implementation of the role/location workflow.

Activities exercise the real Django ORM from worker threads, so these tests
use ``TransactionTestCase`` and the time-skipping Temporal test server.
"""

import asyncio
import json
import os
import queue
import threading
import time
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TransactionTestCase, override_settings
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment

from . import services
from .external_systems import ExternalSystems, FaultPlan
from .models import WorkflowRun, WorkflowStepRun
from .temporal import activities as temporal_activities
from .temporal.activities import RoleChangeActivities
from .temporal.contracts import (
    STEP_ORDER,
    WORKFLOW_FAILED_ERROR,
    RoleChangeInput,
    StepInput,
    workflow_id,
)
from .temporal.worker import build_worker, execute_role_change, start_role_change
from .temporal.workflows import RoleChangeWorkflow
from .tests import STEP_NAMES, make_employee, make_request


class TemporalTestCase(TransactionTestCase):
    def setUp(self):
        self.employee = make_employee()
        self.request = make_request(self.employee)
        self.task_queue = f"employee-lifecycle-test-{uuid4()}"
        self.make_systems()

    def make_systems(self, faults: FaultPlan | None = None):
        self.systems = ExternalSystems.simulated(faults)
        # Legacy state the employee already has in the IdP before the change.
        self.systems.identity.memberships["E1001"] = {
            "all-employees",
            "dept-engineering",
            "region-us",
            "source-control",
            "office-austin-badge",
        }

    def step_statuses(self, run: WorkflowRun) -> dict[str, str]:
        return {s.name: s.status for s in run.steps.all()}

    def execute(self) -> WorkflowRun:
        """Run the workflow to a terminal state on a fresh test env."""

        async def go():
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with build_worker(
                    env.client, self.systems, task_queue=self.task_queue
                ):
                    return await execute_role_change(
                        env.client, self.request.pk, self.task_queue
                    )

        try:
            result = asyncio.run(go())
        except WorkflowFailureError as exc:
            raise exc.cause
        return WorkflowRun.objects.get(pk=result.run_id)

    def failed_run(self) -> tuple[WorkflowRun, ApplicationError]:
        try:
            self.execute()
        except ApplicationError as exc:
            self.assertEqual(exc.type, WORKFLOW_FAILED_ERROR)
            return WorkflowRun.objects.get(request=self.request), exc
        self.fail("expected the workflow to fail")


class HappyPathTests(TemporalTestCase):
    def test_happy_path_updates_all_systems(self):
        run = self.execute()

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
        self.assertIn("benefits_confirmation_id", run.context)

        # Payroll
        payroll = self.systems.payroll.configurations["E1001"]
        self.assertEqual(payroll["tax_jurisdiction"], "CA-PIT")
        self.assertEqual(payroll["pay_group"], "US-SEMIMONTHLY")
        self.assertEqual(payroll["annual_salary_cents"], 21_000_000)
        self.assertEqual(run.context["payroll_confirmation_id"], "PAY-E1001-1")

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
        self.assertEqual(
            event["payload"]["current"]["work_location"], "San Francisco"
        )
        self.assertEqual(
            event["payload"]["payroll_confirmation_id"], "PAY-E1001-1"
        )


class TemporalTransientFailureTests(TemporalTestCase):
    def test_transient_payroll_failure_is_retried_by_temporal(self):
        self.make_systems(FaultPlan(transient={"payroll.update_configuration": 2}))
        started = time.monotonic()
        run = self.execute()
        elapsed = time.monotonic() - started

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        payroll_step = run.steps.get(name="update_payroll")
        self.assertEqual(payroll_step.status, WorkflowStepRun.Status.COMPLETED)
        self.assertEqual(payroll_step.attempts, 3)
        self.assertEqual(payroll_step.last_error, "")
        self.assertEqual(
            self.systems.payroll.call_count("update_configuration"), 3
        )
        self.assertEqual(run.context["payroll_confirmation_id"], "PAY-E1001-1")
        self.assertEqual(run.attempt, 1)
        # Retries are scheduled on the time-skipping clock, not real time.
        self.assertLess(elapsed, 30)

    def test_retry_budget_exhausted_fails_run(self):
        self.make_systems(FaultPlan(transient={"benefits.update_enrollment": 3}))
        run, exc = self.failed_run()

        self.assertEqual(run.status, WorkflowRun.Status.FAILED)
        self.assertIn("recalculate_eligibility", run.last_error)
        self.assertIn("upstream timeout", run.last_error)
        step = run.steps.get(name="recalculate_eligibility")
        self.assertEqual(step.status, WorkflowStepRun.Status.FAILED)
        self.assertEqual(step.attempts, 3)
        # The parallel sibling steps still completed.
        self.assertEqual(
            run.steps.get(name="update_payroll").status,
            WorkflowStepRun.Status.COMPLETED,
        )
        self.assertEqual(
            run.steps.get(name="reconcile_access").status,
            WorkflowStepRun.Status.COMPLETED,
        )
        self.assertEqual(
            run.steps.get(name="notify_downstream").status,
            WorkflowStepRun.Status.PENDING,
        )
        self.assertEqual(self.systems.notifications.published, [])


class TemporalPartialFailureTests(TemporalTestCase):
    def test_permanent_failure_is_not_retried_and_earlier_effects_stay(self):
        self.make_systems(FaultPlan(permanent={"identity.grant"}))
        run, exc = self.failed_run()

        self.assertEqual(run.status, WorkflowRun.Status.FAILED)
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

        self.employee.refresh_from_db()
        self.assertEqual(self.employee.work_state, "CA")
        self.assertIn("E1001", self.systems.benefits.enrollments)
        self.assertIn("E1001", self.systems.payroll.configurations)
        self.assertEqual(self.systems.notifications.published, [])
        self.assertNotIn("access_groups", run.context)

    def test_half_applied_access_reconciliation(self):
        self.make_systems()
        original_grant = self.systems.identity.grant

        def grant(employee_number, group):
            original_grant(employee_number, group)
            self.systems.identity.faults.transient["identity.grant"] = 99

        self.systems.identity.grant = grant

        run, exc = self.failed_run()

        # "leadership" was granted on the first pass; "people-managers" was
        # not, and the stale badge group was never revoked.
        self.assertIn("leadership", self.systems.identity.memberships["E1001"])
        self.assertNotIn(
            "people-managers", self.systems.identity.memberships["E1001"]
        )
        self.assertIn(
            "office-austin-badge", self.systems.identity.memberships["E1001"]
        )


class TemporalRerunTests(TemporalTestCase):
    def test_rerun_after_outage_skips_completed_steps_without_duplicate_side_effects(self):
        self.make_systems(FaultPlan(transient={"identity.list_groups": 3}))
        run, exc = self.failed_run()

        benefits_calls = len(self.systems.benefits.calls)
        payroll_calls = len(self.systems.payroll.calls)
        self.employee.refresh_from_db()
        updated_at_before = self.employee.updated_at

        # Outage clears; operator re-runs the same request.
        run = self.execute()

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

    def test_rerun_after_partial_access_only_issues_missing_calls(self):
        self.make_systems(FaultPlan(permanent={"identity.revoke"}))
        run, exc = self.failed_run()
        # Grants happened, the revoke failed permanently.
        self.assertEqual(self.systems.identity.call_count("grant"), 2)

        self.systems.identity.faults.permanent.clear()
        run = self.execute()

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        # Re-reading current membership meant no grant was repeated.
        self.assertEqual(self.systems.identity.call_count("grant"), 2)
        self.assertEqual(self.systems.identity.call_count("revoke"), 2)
        self.assertEqual(run.context["granted"], [])
        self.assertEqual(run.context["revoked"], ["office-austin-badge"])


class ConcurrencyTests(TemporalTestCase):
    def test_independent_steps_run_concurrently(self):
        self.make_systems()
        barrier = threading.Barrier(3)
        events: list[tuple[str, float, float]] = []
        lock = threading.Lock()

        def guarded(fn, name):
            def inner(*args, **kwargs):
                barrier.wait(timeout=10)
                started = time.monotonic()
                try:
                    return fn(*args, **kwargs)
                finally:
                    with lock:
                        events.append((name, started, time.monotonic()))

            return inner

        self.systems.benefits.update_enrollment = guarded(
            self.systems.benefits.update_enrollment, "recalculate_eligibility"
        )
        self.systems.payroll.update_configuration = guarded(
            self.systems.payroll.update_configuration, "update_payroll"
        )
        self.systems.identity.list_groups = guarded(
            self.systems.identity.list_groups, "reconcile_access"
        )

        timestamps: dict[str, float] = {}
        handlers = temporal_activities.STEP_HANDLERS

        def stamped(handler, name):
            def inner(run, step, systems):
                result = handler(run, step, systems)
                with lock:
                    timestamps[f"{name}:end"] = time.monotonic()
                return result

            return inner

        original_hr = handlers["update_hr_record"]
        original_notify = handlers["notify_downstream"]

        def notify_started(run, step, systems):
            with lock:
                timestamps["notify_downstream:start"] = time.monotonic()
            return original_notify(run, step, systems)

        handlers["update_hr_record"] = stamped(original_hr, "update_hr_record")
        handlers["notify_downstream"] = notify_started
        try:
            run = self.execute()
        finally:
            handlers["update_hr_record"] = original_hr
            handlers["notify_downstream"] = original_notify

        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        self.assertFalse(barrier.broken)
        self.assertEqual(len(events), 3)
        # update_hr_record finished before any parallel step started, and
        # notify_downstream started only after all three finished.
        self.assertLess(
            timestamps["update_hr_record:end"],
            min(started for _, started, _ in events),
        )
        self.assertGreater(
            timestamps["notify_downstream:start"],
            max(finished for _, _, finished in events),
        )


class DuplicateStartTests(TemporalTestCase):
    def test_duplicate_start_for_same_request_is_rejected(self):
        async def go():
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with build_worker(
                    env.client, self.systems, task_queue=self.task_queue
                ):
                    handle = await start_role_change(
                        env.client, self.request.pk, self.task_queue
                    )
                    with self.assertRaises(WorkflowAlreadyStartedError):
                        await start_role_change(
                            env.client, self.request.pk, self.task_queue
                        )
                    return await handle.result()

        result = asyncio.run(go())
        self.assertEqual(
            WorkflowRun.objects.filter(request=self.request).count(), 1
        )
        run = WorkflowRun.objects.get(pk=result.run_id)
        self.assertEqual(run.status, WorkflowRun.Status.COMPLETED)
        self.assertEqual(
            self.systems.payroll.call_count("update_configuration"), 1
        )


class ProgressQueryTests(TemporalTestCase):
    def test_progress_query_reports_completed_steps(self):
        async def go():
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with build_worker(
                    env.client, self.systems, task_queue=self.task_queue
                ):
                    handle = await start_role_change(
                        env.client, self.request.pk, self.task_queue
                    )
                    result = await handle.result()
                    handle = env.client.get_workflow_handle(
                        workflow_id(self.request.pk)
                    )
                    progress = await handle.query(RoleChangeWorkflow.progress)
                    return result, progress

        result, progress = asyncio.run(go())
        self.assertEqual(progress.run_id, result.run_id)
        self.assertEqual(progress.status, "COMPLETED")
        self.assertEqual(progress.completed, list(STEP_ORDER))
        self.assertEqual(progress.current_steps, [])
        self.assertEqual(progress.failed, [])
        self.assertEqual(
            progress.attempts, {name: 1 for name in STEP_ORDER}
        )


class ActivityShortCircuitTests(TemporalTestCase):
    def test_completed_step_activity_short_circuits(self):
        def run_in_thread():
            # ActivityEnvironment invokes the sync activity from async
            # context; this dedicated thread is the only thing affected.
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
            acts = RoleChangeActivities(self.systems)

            async def go():
                env = ActivityEnvironment()
                # Sync activities return their result directly (not awaitable).
                ref = env.run(
                    acts.create_run, RoleChangeInput(request_id=self.request.pk)
                )
                step = WorkflowStepRun.objects.get(
                    run_id=ref.run_id, name="update_payroll"
                )
                step.status = WorkflowStepRun.Status.COMPLETED
                step.result = {"payroll_confirmation_id": "PAY-STORED"}
                step.attempts = 1
                step.save()
                return ref, env.run(
                    acts.run_step,
                    StepInput(
                        run_id=ref.run_id, step="update_payroll", context={}
                    ),
                )

            return asyncio.run(go())

        holder: dict = {}

        def target():
            try:
                holder["out"] = run_in_thread()
            except Exception as exc:  # noqa: BLE001
                holder["exc"] = exc

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=60)
        if "exc" in holder:
            raise holder["exc"]
        ref, out = holder["out"]

        self.assertTrue(out.skipped)
        self.assertEqual(out.result, {"payroll_confirmation_id": "PAY-STORED"})
        self.assertEqual(out.attempts, 1)
        self.assertEqual(
            self.systems.payroll.call_count("update_configuration"), 0
        )


class TemporalApiTests(TemporalTestCase):
    def test_api_in_temporal_mode_returns_completed_run(self):
        ready: queue.Queue = queue.Queue()
        stop = threading.Event()

        def run_env():
            async def main():
                async with await WorkflowEnvironment.start_time_skipping() as env:
                    async with build_worker(
                        env.client, self.systems, task_queue=self.task_queue
                    ):
                        ready.put(env)
                        await asyncio.to_thread(stop.wait)

            asyncio.run(main())

        thread = threading.Thread(target=run_env)
        thread.start()
        env = ready.get(timeout=60)

        async def connect_test_client():
            return await env.connect_client()

        try:
            with (
                override_settings(
                    EMPLOYEE_LIFECYCLE_ENGINE="temporal",
                    TEMPORAL_TASK_QUEUE=self.task_queue,
                ),
                patch.object(
                    services, "connect_client", new=connect_test_client
                ),
            ):
                client = Client()
                response = client.post(
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

                detail = client.get(f"/employee-lifecycle/runs/{body['id']}/")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(len(detail.json()["steps"]), len(STEP_NAMES))
        finally:
            stop.set()
            thread.join(timeout=60)
