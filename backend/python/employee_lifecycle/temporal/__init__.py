"""Temporal implementation of the role/location change workflow.

Mirrors the legacy sequential orchestrator on the Temporal Python SDK:
steps become activities, retries and resumability are handled by Temporal,
and the ``WorkflowRun`` / ``WorkflowStepRun`` tables remain the
application-visible state surfaced by the HTTP API.
"""
