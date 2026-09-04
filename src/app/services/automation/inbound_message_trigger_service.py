"""Trigger matching for workflows that start from an inbound patient reply.

Covers both channels. SMS and email replies had separate triggers with identical
shapes, and the email one was never wired to a service at all — a definition
could name it, and nothing would ever enroll. One trigger, one matcher, and the
channel becomes a field rather than a type.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow
from src.app.services.automation.campaign_conversation_service import _whole_token_match
from src.app.services.automation.definition_schema import (
    InboundMessageTrigger,
    WorkflowDefinition,
)
from src.app.services.automation.trigger_lookup import find_active_workflows


class InboundMessageTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_inbound_message_workflows(
        self,
        institution_id: str,
        *,
        location_id: str | None,
        channel: str = "sms",
    ) -> list[AutomationWorkflow]:
        """Active workflows that start from a reply on ``channel``."""
        return await find_active_workflows(
            self.session,
            institution_id=institution_id,
            trigger_type="inbound_message",
            location_id=location_id,
        )

    async def find_active_sms_reply_workflows(
        self,
        institution_id: str,
        *,
        location_id: str | None,
    ) -> list[AutomationWorkflow]:
        """Backwards-compatible alias for the SMS channel."""
        return await self.find_active_inbound_message_workflows(
            institution_id, location_id=location_id, channel="sms"
        )


def workflow_matches_inbound_message(
    workflow: AutomationWorkflow,
    body: str | None,
    *,
    channel: str = "sms",
) -> bool:
    """True if this reply satisfies the workflow's channel and token filters.

    No tokens means any reply on a subscribed channel enrolls. Tokens match whole
    words, so "YES" does not fire on "yesterday".
    """
    if not workflow.definition:
        return False
    try:
        definition = WorkflowDefinition.model_validate(workflow.definition)
    except Exception:
        return False

    for trigger in definition.triggers:
        if not isinstance(trigger, InboundMessageTrigger):
            continue
        if channel not in trigger.channels:
            continue
        if not trigger.tokens:
            return True
        if any(_whole_token_match(body, token) for token in trigger.tokens):
            return True
    return False


def inbound_message_idempotency_key(
    workflow_version_id: str,
    inbound_message_id: str,
    *,
    channel: str = "sms",
) -> str:
    """Stable dedupe key: one triggered run per inbound message per version.

    The SMS form keeps its original prefix so keys already written stay valid.
    """
    prefix = "sms-reply" if channel == "sms" else f"{channel}-reply"
    return f"{prefix}:{workflow_version_id}:{inbound_message_id}"
