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
    EMAIL = "email"
    VOICE = "voice"


class ConsentStatus(str, Enum):
    GRANTED = "granted"
    REVOKED = "revoked"


class ConsentSource(str, Enum):
    MANUAL = "manual"
    TWILIO_KEYWORD = "twilio_keyword"
    SYSTEM = "system"


class ConsentBasis(str, Enum):
    """Legal basis of a consent record (TCPA/CASL). Marketing-class outreach
    requires an express (written, per FCC) basis; exempt-care/transactional can
    rely on implied/treatment basis. Enforced per content class by the gate."""

    EXPRESS_WRITTEN = "express_written"  # signed/written opt-in (marketing minimum, US)
    EXPRESS = "express"                  # explicit opt-in (e.g. patient-requested callback)
    IMPLIED = "implied"                  # implied from the relationship
    EXEMPT_TREATMENT = "exempt_treatment"  # HIPAA treatment/appointment exemption


class ConsentRecord(Base):
    """Append-style consent state record for an institution-scoped contact.

    The consent *identity* is channel-specific: SMS/VOICE are keyed on
    ``phone_hash``, EMAIL on ``email_hash``. An email-only contact (no phone)
    therefore has a valid email consent basis without a phone number — both
    identity columns are nullable so each channel populates its own.
    """

    __tablename__ = "consent_records"
    __table_args__ = (
        Index("ix_consent_records_institution_channel_phone", "institution_id", "channel", "phone_hash"),
        Index("ix_consent_records_institution_channel_email", "institution_id", "channel", "email_hash"),
        CheckConstraint("channel IN ('sms', 'email', 'voice')", name="ck_consent_records_channel"),
        CheckConstraint("status IN ('granted', 'revoked')", name="ck_consent_records_status"),
        CheckConstraint(
            "source IN ('manual', 'twilio_keyword', 'system')",
            name="ck_consent_records_source",
        ),
        CheckConstraint(
            "basis IS NULL OR basis IN ('express_written', 'express', 'implied', 'exempt_treatment')",
            name="ck_consent_records_basis",
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
    # Channel-specific consent identity. SMS/VOICE key on phone_hash, EMAIL on
    # email_hash — both nullable so an email-only or phone-only contact carries
    # only the identity its channel needs.
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    phone_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    email_masked: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Legal basis (TCPA/CASL). NULL = legacy/unspecified → interpreted as "implied"
    # by the gate, so marketing-class sends require an explicit express(_written) basis.
    basis: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
        CheckConstraint("channel IN ('sms', 'email', 'voice')", name="ck_sms_suppressions_channel"),
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


class DncScope(str, Enum):
    """How wide a do-not-contact record reaches (scope §11 DNC tiers)."""

    LOCATION = "location"        # only the location whose sender received the STOP
    INSTITUTION = "institution"  # every location in the institution
    GROUP = "group"              # privileged DSO-wide "remove me everywhere"


class DoNotContact(Base):
    """Do-not-contact state that blocks outbound outreach on ALL channels.

    Channel-agnostic (a DNC blocks SMS, voice, and email alike). ``scope`` tiers
    how far it reaches: ``location`` (only the location whose number received the
    STOP), ``institution`` (default — every location in the tenant), or ``group``
    (a privileged DSO-wide removal). Existing rows predate ``scope`` and default
    to ``institution`` for backward compatibility.
    """

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
        CheckConstraint(
            "scope IN ('location', 'institution', 'group')",
            name="ck_do_not_contact_scope",
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
    scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DncScope.INSTITUTION.value
    )
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
