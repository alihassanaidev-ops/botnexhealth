"""Inbound email message log.

The email counterpart to :class:`~src.app.models.inbound_sms_message.InboundSmsMessage`,
and the same storage discipline: hashed and masked addresses, an **encrypted**
body, nothing PHI-bearing in clear.

Email needs more than SMS did. Subject lines carry PHI as readily as bodies, so
they are encrypted too. ``Message-ID`` / ``In-Reply-To`` are retained for future
standards-based display threading, but tenant attribution still requires the
signed routing address and never guesses from a header. A message the provider
flags as spam or malware is recorded rather than silently dropped, so an
address that keeps getting quarantined is visible instead of looking like
patient silence.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class InboundEmailIntent(str, Enum):
    """What the reply appears to be asking for."""

    STOP = "stop"
    CONFIRM = "confirm"
    RESCHEDULE = "reschedule"
    QUESTION = "question"
    AUTO_REPLY = "auto_reply"
    FREE_TEXT = "free_text"


class InboundEmailStatus(str, Enum):
    """How far the message got through routing.

    ``QUARANTINED`` covers anything we refused to route — spam, malware, or a
    token that did not verify. Kept rather than dropped: a message we cannot
    place is evidence, and guessing which clinic it belongs to would be a
    cross-tenant disclosure.
    """

    ROUTED = "routed"
    UNROUTABLE = "unroutable"
    QUARANTINED = "quarantined"


class InboundEmailMessage(Base):
    """One inbound email, persisted for the shared inbox and run correlation."""

    __tablename__ = "inbound_email_messages"
    __table_args__ = (
        Index(
            "ix_inbound_email_messages_institution_created",
            "institution_id",
            "created_at",
        ),
        Index("ix_inbound_email_messages_contact", "contact_id"),
        Index("ix_inbound_email_messages_thread", "conversation_thread_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )

    #: Nullable because a message can arrive that we cannot attribute. Guessing
    #: the tenant would be worse than holding it unattributed.
    institution_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
    )
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True
    )
    conversation_thread_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("campaign_conversation_threads.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Provider-side id, used to dedupe redelivery.
    provider_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    #: Where the full MIME is stored, when it was kept.
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    from_email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    from_email_masked: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_email_masked: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # -- Threading -----------------------------------------------------
    # RFC 5322 headers. The only route back to a conversation when the reply
    # arrives without our routing token.
    message_id: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    references: Mapped[str | None] = mapped_column(Text, nullable=True)

    intent: Mapped[str] = mapped_column(
        String(24), nullable=False, default=InboundEmailIntent.FREE_TEXT.value
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=InboundEmailStatus.ROUTED.value
    )
    #: Why a message was quarantined or could not be routed.
    status_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Subjects carry PHI as readily as bodies.
    subject_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    has_attachments: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Provider authentication and content verdicts, kept for triage.
    spam_verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)
    virus_verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)
    spf_verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)
    dkim_verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)
    dmarc_verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)

    #: True when the sender is not the address we mailed — a forwarded message,
    #: a shared family mailbox, an assistant. Identity must not be inferred from
    #: the routing token alone.
    sender_mismatch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def body(self) -> str | None:
        return decrypt_value(self.body_encrypted)

    @body.setter
    def body(self, value: str | None) -> None:
        self.body_encrypted = encrypt_value(value)

    @property
    def subject(self) -> str | None:
        return decrypt_value(self.subject_encrypted)

    @subject.setter
    def subject(self, value: str | None) -> None:
        self.subject_encrypted = encrypt_value(value)

    def __repr__(self) -> str:
        return (
            f"<InboundEmailMessage(id={self.id}, status={self.status}, "
            f"intent={self.intent})>"
        )
