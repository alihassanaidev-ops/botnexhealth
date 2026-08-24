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

from src.app.models.campaign_conversation_thread import CampaignConversationThread
from src.app.models.campaign_response import CampaignStaffHandoff
from src.app.models.contact import Contact
from src.app.models.inbound_email_message import InboundEmailMessage
from src.app.models.inbound_sms_message import InboundSmsMessage
from src.app.models.institution import Institution
from src.app.models.sms_history_log import SmsHistoryLog
from src.app.models.user import User, UserRole

logger = logging.getLogger(__name__)

_ACTIVE_THREAD_STATUSES = ("open", "handoff")
_UNRESOLVED_HANDOFF_STATUSES = ("open", "assigned")


class InboxAccessError(PermissionError):
    """The caller may not see or act on this conversation."""


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
    def may_reply(self) -> bool:
        return self.may_read_content

    @property
    def may_assign(self) -> bool:
        """Staff can read and reply; assignment is an admin action."""
        return self.role in (
            UserRole.SUPER_ADMIN.value,
            UserRole.INSTITUTION_ADMIN.value,
            UserRole.LOCATION_ADMIN.value,
        )


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
            .where(InboundEmailMessage.conversation_thread_id == str(thread.id))
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

        sms = await self.session.execute(
            select(InboundSmsMessage)
            .where(InboundSmsMessage.conversation_thread_id == str(thread.id))
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
        # There is no equivalent for email yet: outbound email is not logged
        # per-message, so an email thread still shows only the inbound side. See
        # the session notes — the same missing log is what blocks in-app replying
        # and Message-ID threading fallback, so all three want fixing together.
        sent = await self.session.execute(
            select(SmsHistoryLog)
            .where(SmsHistoryLog.conversation_thread_id == str(thread.id))
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
        if not scope.may_reply:
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
