"""Durable patient-email ledger used by workflows and the shared inbox."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value
from src.app.services.sms_privacy import hash_email


class OutboundEmailMessage(Base):
    __tablename__ = "outbound_email_messages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','sending','sent','failed','uncertain')",
            name="ck_outbound_email_messages_status",
        ),
        CheckConstraint(
            "source IN ('workflow','inbox','forward')",
            name="ck_outbound_email_messages_source",
        ),
        Index("ix_outbound_email_messages_thread_created", "conversation_thread_id", "created_at"),
        Index("ix_outbound_email_messages_institution_created", "institution_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    institution_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    location_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("institution_locations.id", ondelete="SET NULL"), nullable=True)
    contact_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    workflow_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    conversation_thread_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("campaign_conversation_threads.id", ondelete="CASCADE"), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="inbox")
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    provider: Mapped[str | None] = mapped_column(String(24), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    to_email_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    to_email_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    to_email_masked: Mapped[str] = mapped_column(String(255), nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False)
    subject_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    body_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    @property
    def to_email(self) -> str | None:
        return decrypt_value(self.to_email_encrypted)

    @to_email.setter
    def to_email(self, value: str) -> None:
        self.to_email_encrypted = encrypt_value(value) or ""
        self.to_email_hash = hash_email(value)

    @property
    def subject(self) -> str | None:
        return decrypt_value(self.subject_encrypted)

    @subject.setter
    def subject(self, value: str) -> None:
        self.subject_encrypted = encrypt_value(value) or ""

    @property
    def body(self) -> str | None:
        return decrypt_value(self.body_encrypted)

    @body.setter
    def body(self, value: str) -> None:
        self.body_encrypted = encrypt_value(value) or ""
