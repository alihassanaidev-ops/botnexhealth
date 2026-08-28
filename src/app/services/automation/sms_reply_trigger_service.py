"""Trigger matching for workflows that start from inbound patient SMS replies."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow
from src.app.services.automation.campaign_conversation_service import _whole_token_match
from src.app.services.automation.definition_schema import SmsReplyTrigger, WorkflowDefinition
from src.app.services.automation.trigger_lookup import find_active_workflows


class SmsReplyTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_sms_reply_workflows(
        self,
        institution_id: str,
        *,
        location_id: str | None,
    ) -> list[AutomationWorkflow]:
        """Return active workflows whose trigger type is 'sms_reply' for this sender."""
        return await find_active_workflows(
            self.session,
            institution_id=institution_id,
            trigger_type="sms_reply",
            location_id=location_id,
        )


def workflow_matches_sms_reply(workflow: AutomationWorkflow, body: str | None) -> bool:
    """True if the inbound body satisfies the workflow's optional token filters."""
    if not workflow.definition:
        return False
    try:
        definition = WorkflowDefinition.model_validate(workflow.definition)
    except Exception:
        return False
    trigger = definition.trigger
    if not isinstance(trigger, SmsReplyTrigger):
        return False
    if not trigger.tokens:
        return True
    return any(_whole_token_match(body, token) for token in trigger.tokens)


def sms_reply_idempotency_key(
    workflow_version_id: str,
    inbound_sms_message_id: str,
) -> str:
    """Stable dedupe key: one triggered run per inbound SMS per workflow version."""
    return f"sms-reply:{workflow_version_id}:{inbound_sms_message_id}"
