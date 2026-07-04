"""Disposable appointment projection (Plan 09 D-3 core).

A thin, per-tenant working set of the appointment state we've most recently seen
from NexHealth (webhook or reconciliation). It exists so the engine can:

  * detect a **reschedule** (stored start_time != incoming) to re-enroll at the
    new time — Plan 09 D-1;
  * serve a **freshness window** so dispatch-time revalidation can trust a
    recently-synced row instead of calling NexHealth live on every send — D-2.

It is NOT the system of record — NexHealth is. Rows are cheap to rebuild from a
backfill/reconciliation sweep, and carry only scheduling-relevant, non-clinical
fields. One row per (institution_id, nexhealth_appointment_id).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class AppointmentWorkingSet(Base):
    """Last-seen scheduling state for a NexHealth appointment (per tenant)."""

    __tablename__ = "appointment_working_set"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "nexhealth_appointment_id",
            name="uq_appointment_working_set_appt",
        ),
        Index(
            "ix_appointment_working_set_synced",
            "institution_id",
            "last_synced_at",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
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

    nexhealth_appointment_id: Mapped[str] = mapped_column(String(160), nullable=False)
    nexhealth_patient_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Scheduling state we compare against to detect a reschedule.
    start_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 'scheduled' | 'cancelled' — mirrors the appointment's live disposition.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="scheduled", server_default=text("'scheduled'")
    )
    last_event: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # When we last refreshed this row from NexHealth (webhook or reconciliation).
    # The freshness window compares against this.
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
