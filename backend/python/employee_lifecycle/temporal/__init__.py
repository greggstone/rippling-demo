"""Temporal-based implementation of the role/location change workflow.

- ``contracts``  – names and dataclasses shared by workflow, activities and clients
- ``workflows``  – the durable ``RoleChangeWorkflow`` (deterministic, no I/O)
- ``activities`` – DB writes and remote calls, one activity per legacy step
- ``worker``     – helpers to build a ``Worker`` for the task queue
"""
