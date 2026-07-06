"""Inbound SMS message log (Plan 04 / S-2).

Persists every inbound SMS reply we receive at the Twilio webhook — the durable
record the plan's `inbound_sms_messages` deliverable calls for. Stores only
non-PHI-in-clear metadata: hashed + masked phones and an **encrypted** body
(the reply text is PHI). Each row is intent-classified (stop/start/help/confirm/
free_text) and best-effort correlated to a contact + an open workflow run.

This is a Plan-04-owned message log, NOT a consent table (Plan 12 owns those).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class InboundSmsMessage(Base):
    """One inbound SMS reply, persisted for audit + best-effort run correlation."""

    __tablename__ = "inbound_sms_messages"
    __table_args__ = (
        Index("ix_inbound_sms_messages_institution_created", "institution_id", "created_at"),
        Index("ix_inbound_sms_messages_contact", "contact_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
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
    # Best-effort correlation to an open run (nullable; only set when unambiguous).
    workflow_run_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)

    message_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_phone_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_phone_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # stop | start | help | confirm | free_text
    intent: Mapped[str] = mapped_column(String(20), nullable=False)

    # Encrypted reply body (PHI). Never stored or logged in clear.
    body_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def body(self) -> str | None:
        return decrypt_value(self.body_encrypted)

    @body.setter
    def body(self, value: str | None) -> None:
        self.body_encrypted = encrypt_value(value)
