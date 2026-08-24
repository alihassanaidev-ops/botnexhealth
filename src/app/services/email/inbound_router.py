"""Route a received email to a clinic, a patient and a conversation.

The ordering here is deliberate and the reasoning matters more than the code:

1. **Reject before storing.** Spam and malware are recorded as quarantined but
   never routed, so a hostile message cannot reach a clinic's inbox.
2. **Never guess a tenant.** A message whose routing token does not verify is
   held unattributed. Filing it into the wrong clinic would be a cross-tenant
   disclosure — the worst outcome available here, and worse than losing it.
3. **Honour opt-out before anything else.** A reply saying "stop" is a
   compliance signal first and a message second.
4. **Never auto-reply to a machine.** Auto-responders and bounces are stored and
   stop there.
5. **Identify the conversation, not the person.** The token says which thread a
   reply belongs to; it does not prove who sent it. A reply from an address we
   did not mail is flagged, and PHI is never disclosed on the strength of a
   token alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.campaign_conversation_thread import CampaignConversationThread
from src.app.models.contact import Contact
from src.app.models.inbound_email_message import (
    InboundEmailIntent,
    InboundEmailMessage,
    InboundEmailStatus,
)
from src.app.models.institution import Institution
from src.app.services.email.inbound_parser import ParsedEmail, classify_intent
from src.app.services.email.reply_address import ReplyRoute, find_reply_address, parse_reply_address
from src.app.services.sms_privacy import hash_email

logger = logging.getLogger(__name__)

#: Verdict values the provider uses for a clean message.
_PASSING = {"PASS", "GRAY", "PROCESSING_FAILED", None, ""}


@dataclass(frozen=True)
class InboundVerdicts:
    """Provider-supplied content and authentication checks."""

    spam: str | None = None
    virus: str | None = None
    spf: str | None = None
    dkim: str | None = None
    dmarc: str | None = None

    @property
    def is_hostile(self) -> bool:
        """Spam or malware. Authentication failures alone are not disqualifying —
        plenty of legitimate forwarded mail fails SPF."""
        return (self.spam or "").upper() == "FAIL" or (self.virus or "").upper() == "FAIL"


@dataclass
class RoutingResult:
    message: InboundEmailMessage
    thread: CampaignConversationThread | None = None
    #: Set when the reply should stop the clinic emailing this address.
    suppress_email_hash: str | None = None
    #: Set when a human should look at this.
    needs_staff_attention: bool = False


class InboundEmailRouter:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def route(
        self,
        parsed: ParsedEmail,
        *,
        provider_message_id: str | None = None,
        storage_key: str | None = None,
        verdicts: InboundVerdicts | None = None,
        now: datetime | None = None,
    ) -> RoutingResult | None:
        """Store and route one message. Returns None if it was a duplicate."""
        now = now or datetime.now(timezone.utc)
        verdicts = verdicts or InboundVerdicts()

        if provider_message_id and await self._already_seen(provider_message_id):
            logger.info("inbound email already processed: %s", provider_message_id)
            return None

        from_hash = hash_email(parsed.from_address) if parsed.from_address else None
        message = InboundEmailMessage(
            provider_message_id=provider_message_id,
            storage_key=storage_key,
            from_email_hash=from_hash,
            from_email_masked=_mask_email(parsed.from_address),
            to_email_masked=_mask_email(
                parsed.to_addresses[0] if parsed.to_addresses else None
            ),
            message_id=_truncate(parsed.message_id, 512),
            in_reply_to=_truncate(parsed.in_reply_to, 512),
            references=parsed.references,
            has_attachments=parsed.has_attachments,
            attachment_count=parsed.attachment_count,
            spam_verdict=verdicts.spam,
            virus_verdict=verdicts.virus,
            spf_verdict=verdicts.spf,
            dkim_verdict=verdicts.dkim,
            dmarc_verdict=verdicts.dmarc,
            received_at=parsed.date or now,
        )
        message.subject = parsed.subject
        self.session.add(message)

        # 1. Hostile content never reaches a clinic's inbox.
        if verdicts.is_hostile:
            message.status = InboundEmailStatus.QUARANTINED.value
            message.status_reason = "Failed the provider's spam or virus check"
            await self.session.flush()
            return RoutingResult(message=message)

        # 2. A message we cannot attribute is held rather than guessed at.
        route = self._resolve_route(parsed)
        if route is None:
            message.status = InboundEmailStatus.UNROUTABLE.value
            message.status_reason = "No valid reply address; cannot attribute to a clinic"
            await self.session.flush()
            return RoutingResult(message=message)

        institution = await self._resolve_institution(route)
        if institution is None:
            message.status = InboundEmailStatus.UNROUTABLE.value
            message.status_reason = "Reply address does not match a known clinic"
            await self.session.flush()
            return RoutingResult(message=message)

        message.institution_id = str(institution.id)

        # A flood on the catch-all is stored but not routed, so it cannot bury a
        # clinic's real conversations.
        if from_hash and await self._sender_over_limit(from_hash, now):
            message.status = InboundEmailStatus.QUARANTINED.value
            message.status_reason = "Sender exceeded the hourly inbound limit"
            await self.session.flush()
            return RoutingResult(message=message)

        contact = await self._resolve_contact(institution, route, parsed)
        if contact is not None:
            message.contact_id = str(contact.id)
            message.location_id = _first_id(contact, "location_id")
            # The token proves which conversation, never who is writing.
            message.sender_mismatch = _addresses_differ(contact.email, parsed.from_address)

        # Resolve the run from the token so a reply can resume the workflow that
        # sent the message, rather than only the most recent thread.
        run = await self._resolve_run(institution, route)
        if run is not None:
            message.workflow_run_id = str(run.id)
            if not message.location_id:
                message.location_id = _first_id(run, "location_id")

        body = parsed.body_text or ""
        if len(body.encode("utf-8", "ignore")) > settings.inbound_email_max_body_bytes:
            # Keep the record and the pointer; the full text stays in object
            # storage rather than bloating the row.
            message.body = body[: settings.inbound_email_max_body_bytes]
            message.status_reason = "Body truncated; full message retained in storage"
        else:
            message.body = body

        intent = classify_intent(body, is_auto_reply=parsed.is_auto_reply)
        message.intent = intent
        message.status = InboundEmailStatus.ROUTED.value

        # 3. Opt-out is a compliance signal before it is a message.
        if intent == InboundEmailIntent.STOP.value:
            await self.session.flush()
            return RoutingResult(
                message=message,
                suppress_email_hash=from_hash,
                needs_staff_attention=False,
            )

        # 4. Machines get stored and nothing else.
        if parsed.is_auto_reply or parsed.is_bounce:
            await self.session.flush()
            return RoutingResult(message=message, needs_staff_attention=False)

        thread = await self._attach_thread(message, institution, contact, route, now)
        await self.session.flush()

        return RoutingResult(
            message=message,
            thread=thread,
            # Anything a human wrote that is not a bare confirmation wants a
            # person. Clinical content must never be auto-handled.
            needs_staff_attention=intent != InboundEmailIntent.CONFIRM.value,
        )

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def _resolve_route(self, parsed: ParsedEmail) -> ReplyRoute | None:
        address = find_reply_address(parsed.all_recipients)
        return parse_reply_address(address) if address else None

    async def _resolve_institution(self, route: ReplyRoute) -> Institution | None:
        """Match the institution by id prefix.

        Ids are abbreviated in the address to fit the local-part limit, so this
        is a prefix match — guarded by requiring exactly one match, because an
        ambiguous prefix must not silently pick a tenant.
        """
        prefix = route.institution_prefix
        if not prefix:
            return None
        result = await self.session.execute(
            select(Institution)
            .where(_id_prefix_matches(Institution.id, prefix))
            .limit(2)
        )
        matches = list(result.scalars().all())
        if len(matches) != 1:
            if len(matches) > 1:
                logger.error(
                    "ambiguous institution prefix in reply address: %s", prefix
                )
            return None
        return matches[0]

    async def _resolve_contact(
        self, institution: Institution, route: ReplyRoute, parsed: ParsedEmail
    ) -> Contact | None:
        """Find the contact by id prefix, falling back to the sender address."""
        if route.contact_prefix:
            result = await self.session.execute(
                select(Contact)
                .where(
                    Contact.institution_id == institution.id,
                    _id_prefix_matches(Contact.id, route.contact_prefix),
                )
                .limit(2)
            )
            matches = list(result.scalars().all())
            if len(matches) == 1:
                return matches[0]

        # No usable prefix — a patient writing in fresh rather than replying.
        if parsed.from_address:
            result = await self.session.execute(
                select(Contact)
                .where(
                    Contact.institution_id == institution.id,
                    func.lower(Contact.email) == parsed.from_address.lower(),
                )
                .limit(2)
            )
            matches = list(result.scalars().all())
            if len(matches) == 1:
                return matches[0]
        return None

    async def _resolve_run(self, institution: Institution, route: ReplyRoute):  # noqa: ANN201
        """Find the workflow run the token points at, scoped to the institution."""
        if not route.run_prefix:
            return None
        from src.app.models.automation_workflow import AutomationWorkflowRun

        result = await self.session.execute(
            select(AutomationWorkflowRun)
            .where(
                AutomationWorkflowRun.institution_id == str(institution.id),
                _id_prefix_matches(AutomationWorkflowRun.id, route.run_prefix),
            )
            .limit(2)
        )
        matches = list(result.scalars().all())
        return matches[0] if len(matches) == 1 else None

    async def _attach_thread(
        self,
        message: InboundEmailMessage,
        institution: Institution,
        contact: Contact | None,
        route: ReplyRoute,
        now: datetime,
    ) -> CampaignConversationThread | None:
        """Find the open email thread for this conversation, or start one."""
        if contact is None or not message.location_id:
            # Threads are scoped to a location and a contact; without both there
            # is nothing coherent to attach to. The message is still stored.
            return None

        result = await self.session.execute(
            select(CampaignConversationThread)
            .where(
                CampaignConversationThread.institution_id == str(institution.id),
                CampaignConversationThread.contact_id == str(contact.id),
                CampaignConversationThread.channel == "email",
                CampaignConversationThread.status.in_(("open", "handoff")),
            )
            .order_by(CampaignConversationThread.last_message_at.desc())
            .limit(1)
        )
        thread = result.scalar_one_or_none()

        if thread is None:
            thread = CampaignConversationThread(
                institution_id=str(institution.id),
                location_id=str(message.location_id),
                contact_id=str(contact.id),
                workflow_id=None,
                workflow_run_id=None,
                channel="email",
                status="open",
                opened_at=now,
                last_message_at=now,
            )
            self.session.add(thread)
            await self.session.flush()
        else:
            thread.last_message_at = now

        message.conversation_thread_id = str(thread.id)
        if thread.workflow_run_id:
            message.workflow_run_id = str(thread.workflow_run_id)
        return thread

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------

    async def _already_seen(self, provider_message_id: str) -> bool:
        result = await self.session.execute(
            select(InboundEmailMessage.id).where(
                InboundEmailMessage.provider_message_id == provider_message_id
            )
        )
        return result.scalar_one_or_none() is not None

    async def _sender_over_limit(self, from_hash: str, now: datetime) -> bool:
        since = now - timedelta(hours=1)
        count = await self.session.scalar(
            select(func.count())
            .select_from(InboundEmailMessage)
            .where(
                InboundEmailMessage.from_email_hash == from_hash,
                InboundEmailMessage.created_at >= since,
            )
        )
        return (count or 0) >= settings.inbound_email_sender_hourly_limit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _id_prefix_matches(column, prefix: str):  # noqa: ANN001, ANN202
    """Match a UUID column against the hyphen-free prefix carried in an address.

    The address holds an abbreviated id because the local part is capped, so
    resolution is a prefix match. Callers must require exactly one row: an
    ambiguous prefix has to fail closed rather than pick a tenant.
    """
    return func.replace(func.cast(column, Text), "-", "").like(f"{prefix}%")


def _mask_email(address: str | None) -> str | None:
    """``jane.doe@example.com`` → ``j***@example.com``."""
    if not address or "@" not in address:
        return None
    local, _, domain = address.partition("@")
    if not local:
        return None
    return f"{local[0]}***@{domain}"


def _truncate(value: str | None, length: int) -> str | None:
    if value is None:
        return None
    return value[:length]


def _addresses_differ(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a.strip().lower() != b.strip().lower()


def _first_id(obj: object, attr: str) -> str | None:
    value = getattr(obj, attr, None)
    return str(value) if value else None
