"""SMS-channel view of the inbound-message trigger.

The SMS and email reply triggers merged into one ``inbound_message`` trigger;
the implementation lives in :mod:`inbound_message_trigger_service`. This module
stays as the SMS-shaped entry point so the Twilio webhook route and the existing
tests keep their imports.
"""

from __future__ import annotations

from src.app.models.automation_workflow import AutomationWorkflow
from src.app.services.automation.inbound_message_trigger_service import (
    InboundMessageTriggerService as SmsReplyTriggerService,
)
from src.app.services.automation.inbound_message_trigger_service import (
    inbound_message_idempotency_key,
    workflow_matches_inbound_message,
)

__all__ = [
    "SmsReplyTriggerService",
    "workflow_matches_sms_reply",
    "sms_reply_idempotency_key",
]


def workflow_matches_sms_reply(workflow: AutomationWorkflow, body: str | None) -> bool:
    """True if the inbound SMS body satisfies the workflow's token filters."""
    return workflow_matches_inbound_message(workflow, body, channel="sms")


def sms_reply_idempotency_key(
    workflow_version_id: str,
    inbound_sms_message_id: str,
) -> str:
    """Stable dedupe key: one triggered run per inbound SMS per workflow version."""
    return inbound_message_idempotency_key(
        workflow_version_id, inbound_sms_message_id, channel="sms"
    )
