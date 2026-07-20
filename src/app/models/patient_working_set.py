"""Disposable patient projection for NexHealth campaign matching/contact freshness.

This is not the patient system of record. NexHealth/PMS remains authoritative;
the row only records the last patient state we saw from webhooks or future
backfills so campaign code can reason about freshness without relying on raw
webhook payloads.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class PatientWorkingSet(Base):
    """Last-seen non-clinical state for a NexHealth patient (per tenant)."""

    __tablename__ = "patient_working_set"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "nexhealth_patient_id",
            name="uq_patient_working_set_patient",
        ),
        Index("ix_patient_working_set_synced", "institution_id", "last_synced_at"),
        Index("ix_patient_working_set_contact", "contact_id"),
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
    primary_location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
    )
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    nexhealth_patient_id: Mapped[str] = mapped_column(String(160), nullable=False)
    nexhealth_location_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    preferred_language: Mapped[str | None] = mapped_column(String(32), nullable=True)

    inactive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    unsubscribe_sms: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_new_patient: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    last_event: Mapped[str | None] = mapped_column(String(64), nullable=True)

    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
