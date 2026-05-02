"""SMS consent, suppression, and do-not-contact models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class ConsentChannel(str, Enum):
    SMS = "sms"


class ConsentStatus(str, Enum):
    GRANTED = "granted"
    REVOKED = "revoked"


class ConsentSource(str, Enum):
    MANUAL = "manual"
    TWILIO_KEYWORD = "twilio_keyword"
    SYSTEM = "system"


class ConsentRecord(Base):
    """Append-style consent state record for an institution-scoped phone."""

    __tablename__ = "consent_records"
    __table_args__ = (
        Index("ix_consent_records_institution_channel_phone", "institution_id", "channel", "phone_hash"),
        CheckConstraint("channel IN ('sms')", name="ck_consent_records_channel"),
        CheckConstraint("status IN ('granted', 'revoked')", name="ck_consent_records_status"),
        CheckConstraint(
            "source IN ('manual', 'twilio_keyword', 'system')",
            name="ck_consent_records_source",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institution_locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default=ConsentChannel.SMS.value, index=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phone_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default=ConsentSource.MANUAL.value)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )


class SmsSuppression(Base):
    """Active SMS opt-out/suppression state for an institution-scoped phone."""

    __tablename__ = "sms_suppressions"
    __table_args__ = (
        Index("ix_sms_suppressions_institution_phone_active", "institution_id", "phone_hash", "is_active"),
        Index(
            "uq_sms_suppressions_active_institution_channel_phone",
            "institution_id",
            "channel",
            "phone_hash",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        CheckConstraint("channel IN ('sms')", name="ck_sms_suppressions_channel"),
        CheckConstraint(
            "source IN ('manual', 'twilio_keyword', 'system')",
            name="ck_sms_suppressions_source",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institution_locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default=ConsentChannel.SMS.value, index=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phone_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default=ConsentSource.MANUAL.value)
    keyword: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    released_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DoNotContact(Base):
    """Manual do-not-contact state that blocks outbound SMS."""

    __tablename__ = "do_not_contact"
    __table_args__ = (
        Index("ix_do_not_contact_institution_phone_active", "institution_id", "phone_hash", "is_active"),
        Index(
            "uq_do_not_contact_active_institution_phone",
            "institution_id",
            "phone_hash",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        CheckConstraint(
            "source IN ('manual', 'twilio_keyword', 'system')",
            name="ck_do_not_contact_source",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institution_locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phone_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default=ConsentSource.MANUAL.value)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    released_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
