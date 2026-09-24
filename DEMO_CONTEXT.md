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

## What the demo will do next

A later phase will ask Devin to modernize this workflow (for example onto a
durable execution platform) while keeping the characterization tests in
`employee_lifecycle/tests.py` green. Nothing in this baseline should be
interpreted as a statement about Rippling's architecture.

## Running the tests

```bash
cd backend/python
./venv/bin/python manage.py test
```
