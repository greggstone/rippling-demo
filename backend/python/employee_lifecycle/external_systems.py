"""Simulated downstream systems used by the role change workflow.

These stand in for the benefits carrier gateway, the payroll provider, the
identity provider and the internal event bus. They are in-memory and fully
deterministic so the workflow can be exercised without network access.

Failures are injected through a ``FaultPlan``; by default nothing fails.
Each simulated system also records every call it received so tests (and the
demo) can inspect exactly what the workflow did to the outside world.
"""

from dataclasses import dataclass, field
from typing import Any


class ExternalServiceError(Exception):
    """Base class for simulated remote failures."""


class TransientServiceError(ExternalServiceError):
    """The call may succeed if retried (timeout, 503, throttling...)."""


class PermanentServiceError(ExternalServiceError):
    """The remote system rejected the request; retrying will not help."""


@dataclass
class FaultPlan:
    """Describes which calls should fail.

    ``transient`` maps ``"<system>.<operation>"`` to the number of leading
    calls that should raise ``TransientServiceError`` before succeeding.
    ``permanent`` lists operations that always raise ``PermanentServiceError``.
    """

    transient: dict[str, int] = field(default_factory=dict)
    permanent: set[str] = field(default_factory=set)

    def check(self, operation: str) -> None:
        if operation in self.permanent:
            raise PermanentServiceError(f"{operation}: request rejected by provider")
        remaining = self.transient.get(operation, 0)
        if remaining > 0:
            self.transient[operation] = remaining - 1
            raise TransientServiceError(f"{operation}: upstream timeout")


class SimulatedSystem:
    name = "system"

    def __init__(self, faults: FaultPlan | None = None):
        self.faults = faults or FaultPlan()
        self.calls: list[dict[str, Any]] = []

    def _call(self, operation: str, **payload: Any) -> None:
        self.calls.append({"operation": operation, **payload})
        self.faults.check(f"{self.name}.{operation}")

    def call_count(self, operation: str) -> int:
        return sum(1 for call in self.calls if call["operation"] == operation)


class BenefitsProviderClient(SimulatedSystem):
    """Benefits carrier gateway. Enrollment updates are keyed per employee."""

    name = "benefits"

    def __init__(self, faults: FaultPlan | None = None):
        super().__init__(faults)
        self.enrollments: dict[str, dict[str, Any]] = {}

    def update_enrollment(
        self, employee_number: str, plans: list[str], idempotency_key: str
    ) -> dict[str, Any]:
        self._call(
            "update_enrollment",
            employee_number=employee_number,
            plans=plans,
            idempotency_key=idempotency_key,
        )
        self.enrollments[employee_number] = {
            "plans": sorted(plans),
            "idempotency_key": idempotency_key,
        }
        return {"confirmation_id": f"BEN-{employee_number}-{len(self.calls)}"}


class PayrollProviderClient(SimulatedSystem):
    """Payroll provider.

    The provider deduplicates on ``idempotency_key``: submitting the same key
    twice returns the original confirmation rather than creating a second
    configuration change.
    """

    name = "payroll"

    def __init__(self, faults: FaultPlan | None = None):
        super().__init__(faults)
        self.configurations: dict[str, dict[str, Any]] = {}
        self._confirmations: dict[str, dict[str, Any]] = {}

    def update_configuration(
        self, employee_number: str, config: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        self._call(
            "update_configuration",
            employee_number=employee_number,
            config=config,
            idempotency_key=idempotency_key,
        )
        if idempotency_key in self._confirmations:
            return {**self._confirmations[idempotency_key], "duplicate": True}
        self.configurations[employee_number] = config
        confirmation = {
            "confirmation_id": f"PAY-{employee_number}-{len(self._confirmations) + 1}",
            "duplicate": False,
        }
        self._confirmations[idempotency_key] = confirmation
        return confirmation


class IdentityProviderClient(SimulatedSystem):
    """Identity provider / SSO. Group membership is a set per employee.

    Grants and revocations are separate calls and each can fail
    independently, which is why the access reconciliation step has to be
    careful about partial application.
    """

    name = "identity"

    def __init__(self, faults: FaultPlan | None = None):
        super().__init__(faults)
        self.memberships: dict[str, set[str]] = {}

    def list_groups(self, employee_number: str) -> set[str]:
        self._call("list_groups", employee_number=employee_number)
        return set(self.memberships.get(employee_number, set()))

    def grant(self, employee_number: str, group: str) -> None:
        self._call("grant", employee_number=employee_number, group=group)
        self.memberships.setdefault(employee_number, set()).add(group)

    def revoke(self, employee_number: str, group: str) -> None:
        self._call("revoke", employee_number=employee_number, group=group)
        self.memberships.setdefault(employee_number, set()).discard(group)


class NotificationBusClient(SimulatedSystem):
    """Internal event bus. Consumers dedupe on ``event_id``."""

    name = "notifications"

    def __init__(self, faults: FaultPlan | None = None):
        super().__init__(faults)
        self.published: list[dict[str, Any]] = []

    def publish(self, topic: str, event_id: str, payload: dict[str, Any]) -> None:
        self._call("publish", topic=topic, event_id=event_id)
        if any(event["event_id"] == event_id for event in self.published):
            return
        self.published.append({"topic": topic, "event_id": event_id, "payload": payload})


@dataclass
class ExternalSystems:
    """Bundle of clients handed to the orchestrator."""

    benefits: BenefitsProviderClient
    payroll: PayrollProviderClient
    identity: IdentityProviderClient
    notifications: NotificationBusClient

    @classmethod
    def simulated(cls, faults: FaultPlan | None = None) -> "ExternalSystems":
        faults = faults or FaultPlan()
        return cls(
            benefits=BenefitsProviderClient(faults),
            payroll=PayrollProviderClient(faults),
            identity=IdentityProviderClient(faults),
            notifications=NotificationBusClient(faults),
        )
