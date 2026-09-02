"""The shared conversation inbox.

Reads ``campaign_conversation_threads``, which already carries a ``channel``
column, so SMS and email conversations appear in one place rather than as two
parallel queues. Assignment and resolution reuse ``CampaignStaffHandoff``, which
already has ``assignee_user_id``, ``status`` and ``resolved_at`` — the inbox is
a view over machinery that exists, not a new subsystem beside it.

Scoping is the part that matters. Five roles see five different things, and the
narrowing is enforced here as well as by RLS: a location user must not see
another location's patients, and a group admin must not see message content at
all. That last one is not a UI preference — the group role is deliberately kept
off routes carrying patient information, so it gets counts and response times
and nothing that could identify a patient.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.campaign_conversation_thread import CampaignConversationThread
from src.app.models.campaign_response import CampaignStaffHandoff
from src.app.models.contact import Contact
from src.app.models.inbound_email_message import InboundEmailMessage
from src.app.models.inbound_sms_message import InboundSmsMessage
from src.app.models.outbound_email_message import OutboundEmailMessage
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.sms_history_log import SmsHistoryLog
from src.app.models.user import User, UserRole

logger = logging.getLogger(__name__)

_ACTIVE_THREAD_STATUSES = ("open", "handoff")
_UNRESOLVED_HANDOFF_STATUSES = ("open", "assigned")


class InboxAccessError(PermissionError):
    """The caller may not see or act on this conversation."""


class InboxDeliveryError(RuntimeError):
    """A visible, scoped thread could not safely deliver a staff reply."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class InboxScope:
    """What one caller is allowed to see."""

    role: str
    user_id: str
    institution_id: str | None = None
    location_id: str | None = None
    group_id: str | None = None

    @property
    def is_platform_wide(self) -> bool:
        return self.role == UserRole.SUPER_ADMIN.value

    @property
    def is_group_oversight(self) -> bool:
        return self.role == UserRole.GROUP_ADMIN.value

    @property
    def is_location_bound(self) -> bool:
        """Location admins and staff see only their own location."""
        return self.role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value)

    @property
    def may_read_content(self) -> bool:
        """Group oversight gets activity figures, never message content."""
        return not self.is_group_oversight

    @property
    def may_write(self) -> bool:
        """Who may change a conversation, as opposed to reading one.

        Staff are read-only by design: they work the queue and escalate, but
        assigning and closing a patient conversation are supervisory acts. The
        three admin roles each hold write on their own span — a location admin
        over their location, an institution admin over every location in their
        institution, a super admin over every institution.
        """
        return self.role in (
            UserRole.SUPER_ADMIN.value,
            UserRole.INSTITUTION_ADMIN.value,
            UserRole.LOCATION_ADMIN.value,
        )

    @property
    def may_reply(self) -> bool:
        """Reserved for in-app reply; the same boundary as any other write."""
        return self.may_write

    @property
    def may_assign(self) -> bool:
        return self.may_write

    @property
    def may_resolve(self) -> bool:
        return self.may_write


def scope_for_user(user: User) -> InboxScope:
    return InboxScope(
        role=user.role,
        user_id=str(user.id),
        institution_id=str(user.institution_id) if user.institution_id else None,
        location_id=str(user.location_id) if user.location_id else None,
        group_id=str(getattr(user, "group_id", None)) if getattr(user, "group_id", None) else None,
    )


@dataclass
class ThreadSummary:
    id: str
    channel: str
    status: str
    institution_id: str
    location_id: str | None
    #: Resolved for display. A caller who spans more than one clinic or location
    #: needs to see which one a conversation belongs to; an ID does not tell
    #: them, and the inbox is the one place several tenants appear side by side.
    institution_name: str | None
    location_name: str | None
    contact_id: str | None
    contact_name: str | None
    contact_masked_email: str | None
    last_message_at: datetime | None
    opened_at: datetime | None
    unresolved_handoffs: int
    assignee_user_id: str | None
    latest_intent: str | None
    #: True when the most recent reply came from an address we did not mail.
    sender_mismatch: bool


@dataclass
class ThreadMessage:
    id: str
    direction: str
    channel: str
    body: str | None
    subject: str | None
    intent: str | None
    created_at: datetime | None
    from_masked: str | None
    sender_mismatch: bool = False


class InboxService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        # Name lookups for one request. A platform-wide list can hold threads
        # from many clinics, and re-reading the same institution row per thread
        # would turn one page of the inbox into dozens of queries.
        self._institution_names: dict[str, str | None] = {}
        self._location_names: dict[str, str | None] = {}

    async def _institution_name(self, institution_id: str | None) -> str | None:
        if not institution_id:
            return None
        if institution_id not in self._institution_names:
            row = await self.session.get(Institution, institution_id)
            self._institution_names[institution_id] = (
                getattr(row, "name", None) if row is not None else None
            )
        return self._institution_names[institution_id]

    async def _location_name(self, location_id: str | None) -> str | None:
        if not location_id:
            return None
        if location_id not in self._location_names:
            row = await self.session.get(InstitutionLocation, location_id)
            self._location_names[location_id] = (
                getattr(row, "name", None) if row is not None else None
            )
        return self._location_names[location_id]

    # ------------------------------------------------------------------
    # Scoping
    # ------------------------------------------------------------------

    async def _scoped_threads(self, scope: InboxScope) -> Select:
        """Base query narrowed to what this caller may see."""
        query = select(CampaignConversationThread)

        if scope.is_platform_wide:
            return query

        if scope.is_group_oversight:
            if not scope.group_id:
                # Fail closed: a group admin without a group sees nothing rather
                # than everything.
                return query.where(False)
            member_ids = select(Institution.id).where(
                Institution.group_id == scope.group_id
            )
            return query.where(CampaignConversationThread.institution_id.in_(member_ids))

        if not scope.institution_id:
            return query.where(False)

        query = query.where(
            CampaignConversationThread.institution_id == scope.institution_id
        )
        if scope.is_location_bound:
            if not scope.location_id:
                return query.where(False)
            query = query.where(
                CampaignConversationThread.location_id == scope.location_id
            )
        return query

    async def _load_thread(
        self, scope: InboxScope, thread_id: str
    ) -> CampaignConversationThread:
        base = await self._scoped_threads(scope)
        result = await self.session.execute(
            base.where(CampaignConversationThread.id == thread_id).limit(1)
        )
        thread = result.scalar_one_or_none()
        if thread is None:
            # Indistinguishable from "does not exist", deliberately: confirming a
            # thread exists in another clinic is itself a disclosure.
            raise InboxAccessError("Conversation not found")
        return thread

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_threads(
        self,
        scope: InboxScope,
        *,
        channel: str | None = None,
        status: str | None = None,
        location_id: str | None = None,
        institution_id: str | None = None,
        assigned_to: str | None = None,
        unresolved_only: bool = False,
        search_contact_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ThreadSummary]:
        if not scope.may_read_content:
            raise InboxAccessError("This role sees activity figures, not conversations")

        query = await self._scoped_threads(scope)

        if channel:
            query = query.where(CampaignConversationThread.channel == channel)
        if status:
            query = query.where(CampaignConversationThread.status == status)
        elif unresolved_only:
            query = query.where(
                CampaignConversationThread.status.in_(_ACTIVE_THREAD_STATUSES)
            )
        # A super admin filtering to one clinic, or an institution admin to one
        # of their locations — never a widening of what the scope already allows.
        if institution_id and scope.is_platform_wide:
            query = query.where(
                CampaignConversationThread.institution_id == institution_id
            )
        if location_id and not scope.is_location_bound:
            query = query.where(CampaignConversationThread.location_id == location_id)
        if search_contact_id:
            query = query.where(
                CampaignConversationThread.contact_id == search_contact_id
            )
        if since:
            query = query.where(CampaignConversationThread.last_message_at >= since)

        if assigned_to:
            assigned = select(CampaignStaffHandoff.conversation_thread_id).where(
                CampaignStaffHandoff.assignee_user_id == assigned_to,
                CampaignStaffHandoff.status.in_(_UNRESOLVED_HANDOFF_STATUSES),
            )
            query = query.where(CampaignConversationThread.id.in_(assigned))

        result = await self.session.execute(
            query.order_by(CampaignConversationThread.last_message_at.desc())
            .limit(min(limit, 200))
            .offset(offset)
        )
        threads = list(result.scalars().all())
        return [await self._summarize(thread) for thread in threads]

    async def _summarize(self, thread: CampaignConversationThread) -> ThreadSummary:
        contact = (
            await self.session.get(Contact, thread.contact_id)
            if thread.contact_id
            else None
        )
        handoffs = await self.session.execute(
            select(CampaignStaffHandoff)
            .where(
                CampaignStaffHandoff.conversation_thread_id == str(thread.id),
                CampaignStaffHandoff.status.in_(_UNRESOLVED_HANDOFF_STATUSES),
            )
            .order_by(CampaignStaffHandoff.created_at.desc())
        )
        open_handoffs = list(handoffs.scalars().all())

        latest_intent = None
        mismatch = False
        if thread.channel == "email":
            latest = await self.session.execute(
                select(InboundEmailMessage)
                .where(InboundEmailMessage.conversation_thread_id == str(thread.id))
                .order_by(InboundEmailMessage.created_at.desc())
                .limit(1)
            )
            message = latest.scalar_one_or_none()
            if message is not None:
                latest_intent = message.intent
                mismatch = bool(message.sender_mismatch)

        return ThreadSummary(
            id=str(thread.id),
            channel=thread.channel,
            status=thread.status,
            institution_id=str(thread.institution_id),
            location_id=str(thread.location_id) if thread.location_id else None,
            institution_name=await self._institution_name(str(thread.institution_id)),
            location_name=await self._location_name(
                str(thread.location_id) if thread.location_id else None
            ),
            contact_id=str(thread.contact_id) if thread.contact_id else None,
            contact_name=_contact_name(contact),
            contact_masked_email=_mask(getattr(contact, "email", None)),
            last_message_at=thread.last_message_at,
            opened_at=thread.opened_at,
            unresolved_handoffs=len(open_handoffs),
            assignee_user_id=(
                str(open_handoffs[0].assignee_user_id)
                if open_handoffs and open_handoffs[0].assignee_user_id
                else None
            ),
            latest_intent=latest_intent,
            sender_mismatch=mismatch,
        )

    async def get_messages(
        self, scope: InboxScope, thread_id: str
    ) -> tuple[ThreadSummary, list[ThreadMessage]]:
        if not scope.may_read_content:
            raise InboxAccessError("This role sees activity figures, not conversations")

        thread = await self._load_thread(scope, thread_id)
        messages: list[ThreadMessage] = []

        emails = await self.session.execute(
            select(InboundEmailMessage)
            .where(
                InboundEmailMessage.institution_id == str(thread.institution_id),
                InboundEmailMessage.conversation_thread_id == str(thread.id),
            )
            .order_by(InboundEmailMessage.created_at)
        )
        for message in emails.scalars().all():
            messages.append(
                ThreadMessage(
                    id=str(message.id),
                    direction="inbound",
                    channel="email",
                    body=message.body,
                    subject=message.subject,
                    intent=message.intent,
                    created_at=message.created_at,
                    from_masked=message.from_email_masked,
                    sender_mismatch=bool(message.sender_mismatch),
                )
            )

        outbound_emails = await self.session.execute(
            select(OutboundEmailMessage)
            .where(
                OutboundEmailMessage.institution_id == str(thread.institution_id),
                OutboundEmailMessage.conversation_thread_id == str(thread.id),
                OutboundEmailMessage.status == "sent",
            )
            .order_by(OutboundEmailMessage.created_at)
        )
        for message in outbound_emails.scalars().all():
            messages.append(
                ThreadMessage(
                    id=str(message.id),
                    direction="outbound",
                    channel="email",
                    body=message.body,
                    subject=message.subject,
                    intent=None,
                    created_at=message.sent_at or message.created_at,
                    from_masked=_mask(message.from_address),
                )
            )

        sms = await self.session.execute(
            select(InboundSmsMessage)
            .where(
                InboundSmsMessage.institution_id == str(thread.institution_id),
                InboundSmsMessage.conversation_thread_id == str(thread.id),
            )
            .order_by(InboundSmsMessage.created_at)
        )
        for message in sms.scalars().all():
            messages.append(
                ThreadMessage(
                    id=str(message.id),
                    direction="inbound",
                    channel="sms",
                    body=message.body,
                    subject=None,
                    intent=message.intent,
                    created_at=message.created_at,
                    from_masked=message.from_phone_masked,
                )
            )

        # Outbound SMS, so an SMS conversation reads as a conversation rather
        # than as a list of the patient's replies with our side missing.
        #
        # Email uses OutboundEmailMessage above; SMS keeps its existing history
        # table. Both queries carry the tenant predicate as well as the scoped
        # thread id so RLS is defense-in-depth rather than the only boundary.
        sent = await self.session.execute(
            select(SmsHistoryLog)
            .where(
                SmsHistoryLog.institution_id == str(thread.institution_id),
                SmsHistoryLog.conversation_thread_id == str(thread.id),
            )
            .order_by(SmsHistoryLog.timestamp)
        )
        for message in sent.scalars().all():
            messages.append(
                ThreadMessage(
                    id=str(message.id),
                    direction="outbound",
                    channel="sms",
                    body=_safe_body(message),
                    subject=None,
                    intent=None,
                    created_at=message.timestamp,
                    from_masked=message.to_number_masked,
                )
            )

        messages.sort(key=lambda m: m.created_at or datetime.min.replace(tzinfo=timezone.utc))
        return await self._summarize(thread), messages

    # ------------------------------------------------------------------
    # Filter options
    # ------------------------------------------------------------------

    async def filter_options(self, scope: InboxScope) -> list[dict[str, Any]]:
        """The clinics and locations this caller may narrow the inbox to.

        Deliberately derived from the same scope rules as the thread query, so
        the filter list can never offer a clinic the caller's threads query
        would refuse. It carries names only — a clinic and location directory,
        no patient information — which is why group oversight gets it too: it
        turns their activity figures from bare IDs into practice names without
        crossing the line that role is kept behind.
        """
        institution_ids: list[str] | None = None

        if scope.is_platform_wide:
            institution_ids = None  # every institution
        elif scope.is_group_oversight:
            if not scope.group_id:
                return []
            rows = await self.session.execute(
                select(Institution.id).where(Institution.group_id == scope.group_id)
            )
            institution_ids = [str(r) for r in rows.scalars().all()]
            if not institution_ids:
                return []
        else:
            if not scope.institution_id:
                return []
            institution_ids = [scope.institution_id]

        query = select(Institution.id, Institution.name).order_by(Institution.name)
        if institution_ids is not None:
            query = query.where(Institution.id.in_(institution_ids))
        institutions = (await self.session.execute(query)).all()
        if not institutions:
            return []

        location_query = (
            select(
                InstitutionLocation.id,
                InstitutionLocation.name,
                InstitutionLocation.institution_id,
            )
            .where(
                InstitutionLocation.institution_id.in_(
                    [str(row.id) for row in institutions]
                )
            )
            .order_by(InstitutionLocation.name)
        )
        if scope.is_location_bound:
            # A location user is offered their own location and nothing else,
            # matching what the thread query will actually return.
            if not scope.location_id:
                return []
            location_query = location_query.where(
                InstitutionLocation.id == scope.location_id
            )
        locations = (await self.session.execute(location_query)).all()

        by_institution: dict[str, list[dict[str, str]]] = {}
        for row in locations:
            by_institution.setdefault(str(row.institution_id), []).append(
                {"id": str(row.id), "name": row.name}
            )

        return [
            {
                "id": str(row.id),
                "name": row.name,
                "locations": by_institution.get(str(row.id), []),
            }
            for row in institutions
        ]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def assign(
        self, scope: InboxScope, thread_id: str, assignee_user_id: str | None
    ) -> int:
        if not scope.may_assign:
            raise InboxAccessError("This role cannot assign conversations")
        thread = await self._load_thread(scope, thread_id)

        result = await self.session.execute(
            select(CampaignStaffHandoff).where(
                CampaignStaffHandoff.conversation_thread_id == str(thread.id),
                CampaignStaffHandoff.status.in_(_UNRESOLVED_HANDOFF_STATUSES),
            )
        )
        handoffs = list(result.scalars().all())
        for handoff in handoffs:
            handoff.assignee_user_id = assignee_user_id
            handoff.status = "assigned" if assignee_user_id else "open"
        await self.session.flush()
        return len(handoffs)

    async def resolve(
        self, scope: InboxScope, thread_id: str, *, outcome: str | None = None
    ) -> int:
        """Close the conversation and any open handoffs on it."""
        if not scope.may_resolve:
            raise InboxAccessError("This role cannot resolve conversations")
        thread = await self._load_thread(scope, thread_id)
        now = datetime.now(timezone.utc)

        result = await self.session.execute(
            select(CampaignStaffHandoff).where(
                CampaignStaffHandoff.conversation_thread_id == str(thread.id),
                CampaignStaffHandoff.status.in_(_UNRESOLVED_HANDOFF_STATUSES),
            )
        )
        handoffs = list(result.scalars().all())
        for handoff in handoffs:
            handoff.status = "resolved"
            handoff.resolved_at = now
            handoff.resolution_outcome = outcome

        thread.status = "completed"
        thread.completed_at = now
        thread.completion_reason = outcome or "resolved_by_staff"
        await self.session.flush()
        return len(handoffs)

    async def reply_email(
        self,
        scope: InboxScope,
        thread_id: str,
        *,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> OutboundEmailMessage:
        """Send one recorded email reply without a duplicate-send crash window."""
        if not scope.may_reply:
            raise InboxAccessError("This role cannot reply to conversations")
        thread = await self._load_thread(scope, thread_id)
        if thread.channel != "email":
            raise InboxAccessError("This conversation is not an email thread")

        existing = (
            await self.session.execute(
                select(OutboundEmailMessage).where(
                    OutboundEmailMessage.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if str(existing.conversation_thread_id) != str(thread.id):
                raise InboxAccessError("Reply key belongs to another conversation")
            if existing.status == "sent":
                return existing
            if existing.status in ("sending", "uncertain"):
                raise InboxDeliveryError(
                    "This reply may already be in flight; refresh before trying again",
                    status_code=409,
                )

        contact = await self.session.get(Contact, thread.contact_id)
        if contact is None or not contact.email:
            raise InboxAccessError("This contact has no email address")

        from src.app.services.automation.email_node_executor import get_patient_email_sender_for
        from src.app.services.email.identity_service import EmailIdentityService
        from src.app.services.email.inbox_settings_service import InboxSettingsService
        from src.app.services.email.reply_address import make_reply_address
        from src.app.services.email.sender import EmailMessage, EmailSendError

        configured = await InboxSettingsService(self.session).get(
            str(thread.institution_id),
            str(thread.location_id) if thread.location_id else None,
        )
        if not configured.platform_ready or not configured.is_enabled:
            raise InboxAccessError("Inbound email is not enabled for this location")
        previous_sender_id = (
            await self.session.execute(
                select(OutboundEmailMessage.sender_address_id)
                .where(
                    OutboundEmailMessage.conversation_thread_id == thread.id,
                    OutboundEmailMessage.sender_address_id.is_not(None),
                )
                .order_by(OutboundEmailMessage.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        identity_service = EmailIdentityService(self.session)
        identity = await identity_service.resolve(
            str(thread.institution_id),
            str(thread.location_id) if thread.location_id else None,
            sender_address_id=str(previous_sender_id) if previous_sender_id else None,
        )
        if not identity.from_address and previous_sender_id:
            identity = await identity_service.resolve(
                str(thread.institution_id),
                str(thread.location_id) if thread.location_id else None,
            )
        if not identity.from_address:
            raise InboxAccessError("No sending address is configured")

        ledger = existing or OutboundEmailMessage(
            institution_id=str(thread.institution_id),
            location_id=str(thread.location_id) if thread.location_id else None,
            contact_id=str(contact.id),
            workflow_run_id=str(thread.workflow_run_id) if thread.workflow_run_id else None,
            conversation_thread_id=str(thread.id),
            created_by_user_id=scope.user_id,
            source="inbox",
            idempotency_key=idempotency_key,
            from_address=identity.from_address,
            sender_address_id=identity.address_id,
            to_email_masked=_mask(contact.email) or "***",
            status="sending",
            attempt_count=0,
        )
        ledger.status = "sending"
        ledger.attempt_count += 1
        ledger.error_code = None
        ledger.from_address = identity.from_address
        ledger.sender_address_id = identity.address_id
        ledger.to_email = contact.email
        ledger.subject = subject
        ledger.body = body
        self.session.add(ledger)
        await self.session.commit()

        reply_to = make_reply_address(
            identity.inbound_domain or settings.ses_inbound_domain or "",
            institution_id=str(thread.institution_id),
            location_id=str(thread.location_id) if thread.location_id else None,
            contact_id=str(contact.id),
            workflow_run_id=str(thread.workflow_run_id) if thread.workflow_run_id else None,
        )
        try:
            sent = await get_patient_email_sender_for(identity.provider).send(
                EmailMessage(
                    from_address=identity.from_address,
                    from_name=identity.from_name,
                    to=[contact.email],
                    subject=subject,
                    text=body,
                    reply_to=reply_to,
                    idempotency_key=idempotency_key,
                    institution_id=str(thread.institution_id),
                    tenant_name=identity.tenant_name,
                    configuration_set=identity.configuration_set,
                    tags={"source": "inbox_reply"},
                )
            )
        except EmailSendError as exc:
            ledger.status = "uncertain" if exc.outcome_uncertain else "failed"
            ledger.error_code = type(exc).__name__
            await self.session.commit()
            if exc.outcome_uncertain:
                raise InboxDeliveryError(
                    "Reply outcome is uncertain; do not send it again yet",
                    status_code=409,
                ) from exc
            raise InboxDeliveryError(
                "The email provider did not accept the reply", status_code=502
            ) from exc
        except Exception as exc:
            ledger.status = "uncertain"
            ledger.error_code = type(exc).__name__[:80]
            await self.session.commit()
            raise InboxDeliveryError(
                "Reply outcome is uncertain; do not send it again yet",
                status_code=409,
            ) from exc

        ledger.provider = sent.provider
        ledger.provider_message_id = sent.provider_message_id
        ledger.status = "sent"
        ledger.sent_at = datetime.now(timezone.utc)
        thread.status = "open"
        thread.completed_at = None
        thread.completion_reason = None
        thread.last_message_at = ledger.sent_at
        handoffs = list(
            (
                await self.session.execute(
                    select(CampaignStaffHandoff).where(
                        CampaignStaffHandoff.conversation_thread_id
                        == str(thread.id),
                        CampaignStaffHandoff.status.in_(
                            _UNRESOLVED_HANDOFF_STATUSES
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        for handoff in handoffs:
            handoff.status = "resolved"
            handoff.resolved_at = ledger.sent_at
            handoff.resolution_outcome = "replied_by_staff"
        # The delivery result must be durable before any optional automation
        # cleanup. A cleanup failure must never make the UI invite a duplicate
        # reply after the provider already accepted the first one.
        await self.session.commit()

        if configured.stop_automation_on_reply:
            try:
                await self._stop_automation_for_staff_reply(thread, contact)
                await self.session.commit()
            except Exception as exc:  # noqa: BLE001 — delivery already succeeded
                await self.session.rollback()
                logger.error(
                    "could not stop automation after staff email reply: "
                    "thread=%s contact=%s error=%s",
                    thread.id,
                    contact.id,
                    exc,
                )
        return ledger

    async def _stop_automation_for_staff_reply(
        self, thread: CampaignConversationThread, contact: Contact
    ) -> int:
        """Human takeover cancels every active run for this contact."""
        from src.app.models.automation_workflow import (
            AutomationRunStatus,
            AutomationWorkflowRun,
        )
        from src.app.services.automation.enrollment_service import (
            AutomationWorkflowEnrollmentService,
        )
        from src.app.services.automation.scheduler_service import (
            AutomationWorkflowSchedulerService,
        )

        result = await self.session.execute(
            select(AutomationWorkflowRun).where(
                AutomationWorkflowRun.institution_id == str(thread.institution_id),
                AutomationWorkflowRun.contact_id == str(contact.id),
                AutomationWorkflowRun.status.in_(
                    (
                        AutomationRunStatus.PENDING.value,
                        AutomationRunStatus.RUNNING.value,
                        AutomationRunStatus.WAITING.value,
                    )
                ),
            )
        )
        runs = list(result.scalars().all())
        scheduler = AutomationWorkflowSchedulerService(self.session)
        enrollment = AutomationWorkflowEnrollmentService(self.session)
        for run in runs:
            await scheduler.cancel_timers_for_run(str(run.id))
            await enrollment.cancel_run(run, reason="staff_replied_by_email")
        return len(runs)

    # ------------------------------------------------------------------
    # Group oversight — figures only
    # ------------------------------------------------------------------

    async def activity(
        self, scope: InboxScope, *, days: int = 30
    ) -> dict[str, Any]:
        """Volumes and response times, broken down by clinic and location.

        Contains no message content, no subjects, no patient names and no
        addresses — that is what makes it safe for a role that is otherwise kept
        away from patient information.
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        base = await self._scoped_threads(scope)
        thread_ids = base.with_only_columns(CampaignConversationThread.id)

        totals = await self.session.execute(
            select(
                CampaignConversationThread.institution_id,
                CampaignConversationThread.location_id,
                CampaignConversationThread.channel,
                func.count().label("threads"),
                func.count()
                .filter(
                    CampaignConversationThread.status.in_(_ACTIVE_THREAD_STATUSES)
                )
                .label("open_threads"),
                func.avg(
                    func.extract(
                        "epoch",
                        CampaignConversationThread.completed_at
                        - CampaignConversationThread.opened_at,
                    )
                ).label("avg_resolution_seconds"),
            )
            .select_from(CampaignConversationThread)
            .where(
                CampaignConversationThread.id.in_(thread_ids),
                CampaignConversationThread.opened_at >= since,
            )
            .group_by(
                CampaignConversationThread.institution_id,
                CampaignConversationThread.location_id,
                CampaignConversationThread.channel,
            )
        )

        rows = [
            {
                "institution_id": str(row.institution_id),
                "location_id": str(row.location_id) if row.location_id else None,
                "channel": row.channel,
                "threads": int(row.threads or 0),
                "open_threads": int(row.open_threads or 0),
                "avg_resolution_seconds": (
                    float(row.avg_resolution_seconds)
                    if row.avg_resolution_seconds is not None
                    else None
                ),
            }
            for row in totals.all()
        ]

        unresolved = await self.session.scalar(
            select(func.count())
            .select_from(CampaignStaffHandoff)
            .where(
                CampaignStaffHandoff.conversation_thread_id.in_(thread_ids),
                CampaignStaffHandoff.status.in_(_UNRESOLVED_HANDOFF_STATUSES),
            )
        )

        return {
            "since": since.isoformat(),
            "days": days,
            "breakdown": rows,
            "threads": sum(r["threads"] for r in rows),
            "open_threads": sum(r["open_threads"] for r in rows),
            "unresolved_handoffs": int(unresolved or 0),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contact_name(contact: Contact | None) -> str | None:
    if contact is None:
        return None
    full = getattr(contact, "full_name", None)
    if full:
        return full
    parts = [getattr(contact, "first_name", None), getattr(contact, "last_name", None)]
    joined = " ".join(p for p in parts if p).strip()
    return joined or None


def _safe_body(message: SmsHistoryLog) -> str | None:
    """Read a sent SMS body, tolerating retention having purged it.

    Message bodies have a shorter retention window than the delivery record, so
    an older conversation legitimately has rows whose body is gone. That is the
    retention policy working, not an error — show the gap rather than failing
    the whole thread.
    """
    if getattr(message, "body_purged_at", None):
        return None
    try:
        return message.body
    except Exception:  # noqa: BLE001 — a decrypt failure must not break the view
        logger.warning("could not read sms body for %s", message.id)
        return None


def _mask(address: str | None) -> str | None:
    if not address or "@" not in address:
        return None
    local, _, domain = address.partition("@")
    return f"{local[0]}***@{domain}" if local else None
