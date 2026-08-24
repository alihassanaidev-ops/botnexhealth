"""Run-scoped SMS conversation thread helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationStepStatus,
    AutomationWorkflowRun,
    AutomationWorkflowStepExecution,
    AutomationWorkflowVersion,
)
from src.app.models.campaign_conversation_thread import CampaignConversationThread
from src.app.models.campaign_response import CampaignStaffHandoff
from src.app.services.automation.definition_schema import (
    SendSmsNode,
    SmsReplyWaitSpec,
    SmsResponseMapping,
    WorkflowDefinition,
    sms_reply_wait_spec,
)

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_ACTIVE_THREAD_STATUSES = ("open", "handoff")
_UNRESOLVED_HANDOFF_STATUSES = ("open", "assigned")


@dataclass(frozen=True)
class SmsResponseMappingMatch:
    node_id: str
    mapping: SmsResponseMapping


SmsResponseConfig = SendSmsNode | SmsReplyWaitSpec


class CampaignConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def open_sms_thread(
        self,
        run: AutomationWorkflowRun,
    ) -> CampaignConversationThread:
        """Open or reuse the active SMS thread for a workflow run."""
        existing = (
            await self.session.execute(
                select(CampaignConversationThread).where(
                    CampaignConversationThread.workflow_run_id == str(run.id),
                    CampaignConversationThread.channel == "sms",
                    CampaignConversationThread.status.in_(_ACTIVE_THREAD_STATUSES),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        thread = CampaignConversationThread(
            institution_id=str(run.institution_id),
            location_id=str(run.location_id),
            contact_id=str(run.contact_id),
            workflow_id=str(run.workflow_id),
            workflow_run_id=str(run.id),
            channel="sms",
            status="open",
        )
        self.session.add(thread)
        await self.session.flush()
        return thread

    async def resolve_sms_thread(
        self,
        *,
        institution_id: str,
        location_id: str | None,
        contact_id: str | None,
    ) -> CampaignConversationThread | None:
        """Resolve an inbound SMS reply to exactly one active thread."""
        if not location_id:
            return None

        if not contact_id:
            return None
        result = await self.session.execute(
            select(CampaignConversationThread).where(
                CampaignConversationThread.institution_id == institution_id,
                CampaignConversationThread.location_id == location_id,
                CampaignConversationThread.contact_id == contact_id,
                CampaignConversationThread.channel == "sms",
                CampaignConversationThread.status.in_(_ACTIVE_THREAD_STATUSES),
            )
        )
        threads = [
            thread
            for thread in result.scalars().all()
            if await self._thread_accepts_response(thread)
        ]
        return threads[0] if len(threads) == 1 else None

    async def mark_message_seen(self, thread: CampaignConversationThread) -> None:
        thread.last_message_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def close_terminal_threads_for_run(
        self,
        run: AutomationWorkflowRun,
        *,
        completion_reason: str,
    ) -> None:
        if run.status not in {
            AutomationRunStatus.COMPLETED.value,
            AutomationRunStatus.CANCELLED.value,
            AutomationRunStatus.FAILED.value,
        }:
            return
        result = await self.session.execute(
            select(CampaignConversationThread).where(
                CampaignConversationThread.workflow_run_id == str(run.id),
                CampaignConversationThread.channel == "sms",
                CampaignConversationThread.status.in_(_ACTIVE_THREAD_STATUSES),
            )
        )
        threads = list(result.scalars().all())
        if not threads:
            return
        now = datetime.now(timezone.utc)
        for thread in threads:
            has_handoff = (
                await self.session.execute(
                    select(CampaignStaffHandoff.id)
                    .where(
                        CampaignStaffHandoff.conversation_thread_id == str(thread.id),
                        CampaignStaffHandoff.status.in_(_UNRESOLVED_HANDOFF_STATUSES),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if has_handoff:
                thread.status = "handoff"
                continue
            thread.status = "completed"
            thread.completed_at = now
            thread.completion_reason = completion_reason
        await self.session.flush()

    async def match_sms_response_mapping(
        self,
        *,
        workflow_run_id: str | None,
        body: str | None,
    ) -> SmsResponseMappingMatch | None:
        if not workflow_run_id:
            return None
        run = await self.session.get(AutomationWorkflowRun, workflow_run_id)
        if run is None:
            return None
        version = await self.session.get(AutomationWorkflowVersion, run.workflow_version_id)
        if version is None:
            return None
        definition = WorkflowDefinition.model_validate(version.definition)
        node = await self._response_config_node(run, definition)
        if node is None:
            return None
        for mapping in node.response_mappings:
            if any(_whole_token_match(body, token) for token in mapping.tokens):
                node_id = node.id if isinstance(node, SendSmsNode) else node.node_id
                return SmsResponseMappingMatch(node_id=node_id, mapping=mapping)
        return None

    async def _response_config_node(
        self,
        run: AutomationWorkflowRun,
        definition: WorkflowDefinition,
    ) -> SmsResponseConfig | None:
        explicit_wait = self._current_sms_reply_wait(run, definition)
        if explicit_wait is not None:
            return explicit_wait
        return await self._response_sms_node(run, definition)

    def _current_sms_reply_wait(
        self,
        run: AutomationWorkflowRun,
        definition: WorkflowDefinition,
    ) -> SmsReplyWaitSpec | None:
        node_by_id = {node.id: node for node in definition.nodes}
        current = node_by_id.get(run.current_step_id or "")
        return sms_reply_wait_spec(current)

    async def _response_sms_node(
        self,
        run: AutomationWorkflowRun,
        definition: WorkflowDefinition,
    ) -> SendSmsNode | None:
        node_by_id = {node.id: node for node in definition.nodes}
        step_id = (
            await self.session.execute(
                select(AutomationWorkflowStepExecution.step_id)
                .where(
                    AutomationWorkflowStepExecution.workflow_run_id == str(run.id),
                    AutomationWorkflowStepExecution.step_type == "send_sms",
                    AutomationWorkflowStepExecution.status == AutomationStepStatus.COMPLETED.value,
                    AutomationWorkflowStepExecution.result_code == "sent",
                )
                .order_by(AutomationWorkflowStepExecution.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if step_id:
            node = node_by_id.get(str(step_id))
            return node if isinstance(node, SendSmsNode) else None

        # Unit-test and hand-built runs sometimes lack step execution history.
        # Fall back to the SMS node immediately before the current waiting node.
        candidates = [
            node
            for node in definition.nodes
            if isinstance(node, SendSmsNode) and node.next_node_id == run.current_step_id
        ]
        return candidates[0] if len(candidates) == 1 else None

    async def _thread_accepts_response(
        self,
        thread: CampaignConversationThread,
    ) -> bool:
        run = await self.session.get(AutomationWorkflowRun, str(thread.workflow_run_id))
        if run is None:
            return False
        version = await self.session.get(AutomationWorkflowVersion, run.workflow_version_id)
        if version is None:
            return False
        definition = WorkflowDefinition.model_validate(version.definition)
        node = await self._response_config_node(run, definition)
        window_seconds = node.response_window_seconds if node is not None else 72 * 60 * 60
        anchor = thread.last_message_at or thread.opened_at
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) <= anchor + timedelta(seconds=window_seconds):
            return True
        thread.status = "completed"
        thread.completed_at = datetime.now(timezone.utc)
        thread.completion_reason = "response_window_expired"
        await self.session.flush()
        return False

def _whole_token_match(body: str | None, token: str) -> bool:
    cleaned = token.strip()
    if not body or not cleaned:
        return False
    body_tokens = [item.upper() for item in _WORD_RE.findall(body)]
    token_tokens = [item.upper() for item in _WORD_RE.findall(cleaned)]
    if not token_tokens:
        return False
    width = len(token_tokens)
    return any(body_tokens[index : index + width] == token_tokens for index in range(len(body_tokens)))
