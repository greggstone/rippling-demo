# Demo Context: `employee_lifecycle`

**This workflow is synthetic.** Everything under `backend/python/employee_lifecycle/`
was written specifically for a software-modernization demonstration. It is
**not** Rippling production code, it was not derived from Rippling's internal
systems, and it does not describe how Rippling actually implements any
feature. It lives in this public starter repository only so that the demo has
a realistic-looking Django codebase to work in.

## Why this scenario

The demo needs a workflow that is small enough to read in a few minutes but
that still exhibits the operational problems real home-grown orchestration
develops over time. We chose "an existing employee changes role and work
location" because it resembles the kind of durable, cross-system workflow an
HR/IT/finance platform like Rippling supports: a single business event that
must fan out to several systems of record and be completed reliably even when
one of them is temporarily unavailable.

## What the workflow does

A `RoleChangeRequest` triggers a `WorkflowRun` that executes these steps
strictly in order:

1. `update_hr_record` – apply the new title / department / level / location /
   salary to the core `Employee` record (local DB).
2. `recalculate_eligibility` – recompute benefits plans and policy
   assignments, push the enrollment to the benefits provider.
3. `update_payroll` – derive pay group / tax jurisdiction / salary and send it
   to the payroll provider.
4. `reconcile_access` – compute required identity groups, then grant/revoke
   memberships one call at a time.
5. `notify_downstream` – publish a change event to the internal event bus.

All external systems (`external_systems.py`) are in-memory simulations that
behave deterministically; failures are injected explicitly via `FaultPlan`.
There are no network dependencies.

## The "legacy" characteristics on purpose

The implementation is deliberately the *current* style, not the modernized
one. It is reasonable production-grade code, but it carries the limitations
typical of hand-rolled orchestration:

- explicit sequential orchestration in `orchestrator.py`
- workflow state managed by the application in `WorkflowRun` / `WorkflowStepRun`
- hand-written retry loop with fixed backoff for remote calls
- explicit resume/recovery path (`resume()` and the
  `resume_failed_workflows` management command) after partial failure
- observability limited to log lines and the state tables
- side-effecting remote calls where idempotency has to be handled by hand
  (idempotency keys, re-reading remote state before acting)
- steps 2, 3 and 4 only depend on step 1 yet are executed serially

## Modernized implementation: `employee_lifecycle/temporal/`

The same business steps now also run as a durable Temporal workflow
(`temporalio` Python SDK):

| Legacy concern (`orchestrator.py`)            | Temporal equivalent                                                     |
| --------------------------------------------- | ----------------------------------------------------------------------- |
| sequential `for name, handler in STEP_SEQUENCE` | `RoleChangeWorkflow`: HR update → `asyncio.gather` of eligibility / payroll / access → notify |
| `_run_step_with_retries` + `BACKOFF_SECONDS`  | activity `RetryPolicy` (3 attempts, 0.5s ×4 backoff, `PermanentServiceError` non-retryable) |
| `resume()` / `resume_failed_workflows`        | start the workflow again for the request (`ALLOW_DUPLICATE_FAILED_ONLY`); activities skip COMPLETED steps |
| `run.status == RUNNING` guard                 | workflow id `role-change-<request>` – Temporal rejects a second concurrent run |
| logging + state tables                        | Temporal event history, `progress` query, plus the same `WorkflowRun` / `WorkflowStepRun` projection for the HTTP API and admin |

- `contracts.py` – activity names, retry policies/timeouts, dataclass payloads (sandbox-safe, no Django)
- `workflows.py` – `RoleChangeWorkflow` (no I/O; fan-out waits for every branch to settle before failing)
- `activities.py` – one activity per step, wrapping the unchanged handlers in `steps.py` with step-row bookkeeping and the idempotency guard
- `worker.py` – `build_worker()` and `execute_role_change()`; `manage.py run_temporal_worker` runs a worker against `TEMPORAL_ADDRESS`

Behavioural difference to be aware of: because steps 2–4 fan out, a failure in
one of them no longer leaves its siblings `PENDING` – they run to completion
and the run is marked `FAILED` only once all three have settled.

The legacy orchestrator and its characterization tests are kept unchanged as
the baseline; the HTTP views still use it until a Temporal service is part of
the deployment. Nothing here should be interpreted as a statement about
Rippling's architecture.

## Running the tests

```bash
cd backend/python
./venv/bin/python manage.py test
```

`employee_lifecycle/test_temporal.py` uses Temporal's time-skipping test
server (`WorkflowEnvironment.start_time_skipping()`), which the SDK downloads
on first use; no separately managed Temporal service is required.

To run the durable workflow for real:

```bash
temporal server start-dev                     # in another terminal
./venv/bin/python manage.py run_temporal_worker
```
