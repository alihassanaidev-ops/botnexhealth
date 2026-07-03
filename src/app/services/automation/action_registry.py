"""Action + trigger registries — the extensibility seams (Plan 01 §Services).

The dispatcher no longer hardcodes an ``if/isinstance`` chain for channel sends:
send node types map to executor classes here, so a new channel (e.g. voice once
Plan 03 lands) plugs in by registering an executor instead of editing the core
dispatcher. Each executor exposes
``async def execute(run, node, context) -> next_node_id``.

Trigger types are likewise enumerated here so validation/UI can discover the
supported enrollment sources from one place.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.services.automation.email_node_executor import EmailNodeExecutor
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.sms_node_executor import SmsNodeExecutor


class ActionExecutor(Protocol):
    def __init__(
        self, session: AsyncSession, runtime: AutomationWorkflowRuntimeService
    ) -> None: ...

    async def execute(self, run: AutomationWorkflowRun, node: object, context: dict) -> str: ...


# node.type -> executor class. Voice (send_voice) registers when Plan 03 provides
# the outbound Retell executor; until then the dispatcher falls back to the stub.
_ACTION_EXECUTORS: dict[str, type] = {
    "send_sms": SmsNodeExecutor,
    "send_email": EmailNodeExecutor,
}


def register_action_executor(node_type: str, executor_cls: type) -> None:
    """Register (or override) the executor for a send node type."""
    _ACTION_EXECUTORS[node_type] = executor_cls


def get_action_executor(node_type: str) -> type | None:
    """Return the executor class for a send node type, or None if unregistered."""
    return _ACTION_EXECUTORS.get(node_type)


def supported_action_types() -> frozenset[str]:
    return frozenset(_ACTION_EXECUTORS)


# Trigger types the engine can enroll from (discovery for validation/UI).
SUPPORTED_TRIGGER_TYPES: frozenset[str] = frozenset(
    {"appointment_offset", "recall_scan", "manual", "bulk_import"}
)
