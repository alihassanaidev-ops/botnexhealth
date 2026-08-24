"""Trigger matching for workflows that start from inbound patient SMS replies."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.models.automation_workflow import AutomationWorkflow, AutomationWorkflowStatus
from src.app.services.automation.campaign_conversation_service import _whole_token_match
from src.app.services.automation.definition_schema import SmsReplyTrigger, WorkflowDefinition


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
        result = await self.session.execute(
            select(AutomationWorkflow)
            .options(selectinload(AutomationWorkflow.current_version))
            .where(
                AutomationWorkflow.institution_id == institution_id,
                AutomationWorkflow.status == AutomationWorkflowStatus.ACTIVE.value,
                AutomationWorkflow.current_version_id.is_not(None),
            )
        )
        workflows: list[AutomationWorkflow] = []
        for wf in result.scalars().all():
            if wf.trigger_type != "sms_reply":
                continue
            if wf.location_id is not None and str(wf.location_id) != str(location_id):
                continue
            workflows.append(wf)
        return workflows


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
