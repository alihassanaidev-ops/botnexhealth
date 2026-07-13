"""Action registry — the send-executor lookup seam (Plan 01 §Services).

The dispatcher does not hardcode an ``if/isinstance`` chain for channel sends:
send node types map to executor classes here, and the dispatcher resolves them
via ``get_action_executor``. Each executor exposes
``async def execute(run, node, context) -> next_node_id``.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.services.automation.email_node_executor import EmailNodeExecutor
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.sms_node_executor import SmsNodeExecutor
from src.app.services.automation.voice_node_executor import VoiceNodeExecutor


class ActionExecutor(Protocol):
    def __init__(
        self, session: AsyncSession, runtime: AutomationWorkflowRuntimeService
    ) -> None: ...

    async def execute(self, run: AutomationWorkflowRun, node: object, context: dict) -> str: ...


# node.type -> executor class. All three launch channels are live; a new channel
# plugs in by registering its executor here (no dispatcher edit required).
_ACTION_EXECUTORS: dict[str, type] = {
    "send_sms": SmsNodeExecutor,
    "send_email": EmailNodeExecutor,
    "send_voice": VoiceNodeExecutor,  # Plan 03 (Retell create-phone-call)
}


def get_action_executor(node_type: str) -> type | None:
    """Return the executor class for a send node type, or None if unregistered."""
    return _ACTION_EXECUTORS.get(node_type)
