"""Inbound sales enquiries (Item 21).

The other three campaigns act on people already in the practice's records. Sales
Qualification starts with someone who is not a patient yet — a name and a way to
reach them, submitted from the clinic's own site — so there was nowhere to put
them, and Item 24 could not begin.

Their contact details are personal information belonging to someone who has not
consented to anything, so they get the same treatment as a patient contact:
AES-256-GCM at the application level, with a keyed hash beside the phone so an
enquiry can be matched to an inbound call without decrypting every row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value
from src.app.services.sms_privacy import hash_phone


class EnquiryStatus(str, Enum):
    """Where an enquiry has got to. Set by the campaign as it runs."""

    NEW = "new"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    UNREACHABLE = "unreachable"
    BOOKED = "booked"
    HANDED_TO_STAFF = "handed_to_staff"


class CampaignEnquiry(Base):
    """One inbound enquiry, scoped to a clinic and location like every other record."""

    __tablename__ = "campaign_enquiries"
    __table_args__ = (
        # Re-submitting the same enquiry must not enrol the person twice. The
        # intake key is supplied by the submitting form; uniqueness is per
        # institution so two clinics can't collide on a shared key.
        UniqueConstraint(
            "institution_id", "intake_key", name="uq_campaign_enquiries_intake_key"
        ),
        Index("ix_campaign_enquiries_institution_status", "institution_id", "status"),
        Index("ix_campaign_enquiries_institution_phone", "institution_id", "phone_hash"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Supplied by the submitting source; the deduplication key.
    intake_key: Mapped[str] = mapped_column(String(160), nullable=False)
    #: Which permitted source submitted this (Decision C: a signed webhook).
    source: Mapped[str] = mapped_column(String(80), nullable=False)

    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Personal details of a non-patient — encrypted at rest, as patient contacts are.
    email_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Keyed HMAC, not reversible. Lets an inbound call match without decrypting.
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EnquiryStatus.NEW.value
    )
    #: Set once the enquiry is matched to someone already in the practice's
    #: records, so conversion never creates a duplicate patient.
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    #: The campaign run handling this enquiry.
    workflow_run_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @property
    def email(self) -> str | None:
        return decrypt_value(self.email_encrypted)

    @email.setter
    def email(self, value: str | None) -> None:
        self.email_encrypted = encrypt_value(value)

    @property
    def phone(self) -> str | None:
        return decrypt_value(self.phone_encrypted)

    @phone.setter
    def phone(self, value: str | None) -> None:
        self.phone_encrypted = encrypt_value(value)
        # Kept in step with the encrypted value so the two can never disagree.
        self.phone_hash = hash_phone(value) if value else None
