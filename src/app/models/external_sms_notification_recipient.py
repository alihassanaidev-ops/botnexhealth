"""External SMS notification recipients configured per institution.

No-PMS clinics can add staff/office phone numbers that should receive
automated appointment-request SMS alerts. Phone numbers are encrypted at rest;
the hash supports duplicate detection without plaintext lookup.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from enum import Enum

from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value
from src.app.services.sms_privacy import hash_phone, mask_phone, normalize_phone


class StaffSmsAlertType(str, Enum):
    """Staff-facing SMS alerts a phone number can subscribe to.

    Values mirror ``EmailTemplateType`` so the SMS and email preference screens
    offer the same three switches. Unlike ``SmsTemplateType`` these have no
    editable template — the bodies are built in code and are deliberately
    PHI-free, because they go to arbitrary staff numbers rather than to the
    patient.
    """

    APPOINTMENT_REQUEST = "appointment_request"
    CALL_SUMMARY = "call_summary"
    URGENT_ALERT = "urgent_alert"


class ExternalSmsNotificationRecipient(Base):
    """An external phone number that receives no-PMS automated SMS alerts."""

    __tablename__ = "external_sms_notification_recipients"
    __table_args__ = (
        # One subscription per (location, type). NULL location_id means the
        # whole institution; Postgres treats NULLs as distinct in a unique
        # index, so the two cases need separate partial indexes.
        Index(
            "ix_ext_sms_recipient_institution_phone_type",
            "institution_id",
            "location_id",
            "phone_number_hash",
            "notification_type",
            unique=True,
            postgresql_where=text("location_id IS NOT NULL"),
        ),
        Index(
            "ix_ext_sms_recipient_institution_phone_type_all_locs",
            "institution_id",
            "phone_number_hash",
            "notification_type",
            unique=True,
            postgresql_where=text("location_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # NULL = alerts for every location; set = that location's calls only.
    # Mirrors how staff email recipients are scoped by location.
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    phone_number_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    phone_number_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phone_number_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def phone_number(self) -> str | None:
        return decrypt_value(self.phone_number_encrypted)

    @phone_number.setter
    def phone_number(self, value: str | None) -> None:
        normalized = normalize_phone(value)
        phone_hash = hash_phone(normalized)
        if not normalized or not phone_hash:
            raise ValueError("phone_number must be a valid phone number")
        self.phone_number_encrypted = encrypt_value(normalized)  # type: ignore[assignment]
        self.phone_number_hash = phone_hash
        self.phone_number_masked = mask_phone(normalized)

    def __repr__(self) -> str:
        return (
            f"<ExternalSmsNotificationRecipient(id={self.id}, "
            f"type={self.notification_type}, active={self.is_active})>"
        )
