"""Inbound SMS routing (Plan 04 / S-2).

Persists every inbound SMS reply as an `InboundSmsMessage` (encrypted body,
hashed/masked phones, intent), best-effort correlated to a contact and — only
when unambiguous — to an open workflow run. v1 boundary: this does NOT interpret
free text (no NLU). Free-text replies are surfaced to staff as a notification by
the caller; only template-defined keywords (handled elsewhere) drive a workflow
event.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationRunStatus, AutomationWorkflowRun
from src.app.models.contact import Contact
from src.app.models.inbound_sms_message import InboundSmsMessage
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
        commit). Correlation is best-effort: `workflow_run_id` is set only when
        exactly one WAITING run matches (Decision 2 — never guess the run).
        """
        from_hash = hash_phone(from_number)
        contact_id = await self._resolve_contact_id(institution_id, from_hash)
        workflow_run_id = await self._resolve_unambiguous_run_id(
            institution_id, location_id, contact_id
        )

        msg = InboundSmsMessage(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=contact_id,
            workflow_run_id=workflow_run_id,
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

    async def _resolve_contact_id(
        self, institution_id: str, from_hash: str | None
    ) -> str | None:
        if not from_hash:
            return None
        result = await self.session.execute(
            select(Contact.id).where(
                Contact.institution_id == institution_id,
                Contact.phone_hash == from_hash,
            )
        )
        ids = result.scalars().all()
        # Exactly one contact → correlate; a shared-phone ambiguity stays uncorrelated.
        return str(ids[0]) if len(ids) == 1 else None

    async def _resolve_unambiguous_run_id(
        self, institution_id: str, location_id: str | None, contact_id: str | None
    ) -> str | None:
        if not contact_id or not location_id:
            return None
        result = await self.session.execute(
            select(AutomationWorkflowRun.id).where(
                AutomationWorkflowRun.institution_id == institution_id,
                AutomationWorkflowRun.location_id == location_id,
                AutomationWorkflowRun.contact_id == contact_id,
                AutomationWorkflowRun.status == AutomationRunStatus.WAITING.value,
            )
        )
        ids = result.scalars().all()
        # Exactly one WAITING run → link; zero or several → leave null (staff notified).
        return str(ids[0]) if len(ids) == 1 else None
