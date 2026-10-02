"""Inbound SMS routing (Plan 04 / S-2).

Persists every inbound SMS reply as an `InboundSmsMessage` (encrypted body,
hashed/masked phones, intent), best-effort correlated to a contact and — only
when unambiguous — to an active run-scoped SMS conversation thread. v1 boundary:
this does NOT interpret free text (no NLU). Free-text replies are surfaced to
staff as a notification by the caller; only deterministic keywords/mappings
drive a workflow event.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.contact import Contact
from src.app.models.inbound_sms_message import InboundSmsMessage
from src.app.services.automation.campaign_conversation_service import (
    CampaignConversationService,
)
from src.app.services.sms_privacy import hash_phone, mask_phone

logger = logging.getLogger(__name__)


class InboundSmsRoutingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_inbound(
        self,
        *,
        institution_id: str,
        location_id: str | None,
        from_number: str | None,
        to_number: str | None,
        body: str | None,
        intent: str,
        message_sid: str | None = None,
    ) -> InboundSmsMessage:
        """Persist one inbound reply, best-effort correlating contact + open run.

        The row is added + flushed on the caller's session (the caller owns the
        commit). Correlation is best-effort: all contacts sharing the inbound
        phone are considered, but `workflow_run_id` is set only when exactly
        one reply-eligible conversation thread matches (never guess the run).
        """
        from_hash = hash_phone(from_number)
        contact_ids = await self._resolve_contact_ids(institution_id, from_hash)
        contact_id = str(contact_ids[0]) if len(contact_ids) == 1 else None
        conversation = await CampaignConversationService(self.session).resolve_sms_thread(
            institution_id=institution_id,
            location_id=location_id,
            contact_ids=contact_ids,
        )
        if conversation is not None:
            contact_id = str(conversation.contact_id)
            workflow_run_id = str(conversation.workflow_run_id)
            conversation_thread_id = str(conversation.id)
            await CampaignConversationService(self.session).mark_message_seen(conversation)
        else:
            workflow_run_id = None
            conversation_thread_id = None

        msg = InboundSmsMessage(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=contact_id,
            workflow_run_id=workflow_run_id,
            conversation_thread_id=conversation_thread_id,
            message_sid=message_sid,
            from_phone_hash=from_hash,
            from_phone_masked=mask_phone(from_number),
            to_phone_masked=mask_phone(to_number),
            intent=intent,
        )
        msg.body = body  # encrypts
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def _resolve_contact_ids(
        self, institution_id: str, from_hash: str | None
    ) -> list[str]:
        if not from_hash:
            return []
        result = await self.session.execute(
            select(Contact.id).where(
                Contact.institution_id == institution_id,
                Contact.phone_hash == from_hash,
            )
        )
        return [str(contact_id) for contact_id in result.scalars().all()]
