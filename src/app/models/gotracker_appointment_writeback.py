"""Pending GoTracker appointment writeback state.

Tracks appointment mutations initiated by ScaleNexus until the GoTracker
Synchronizer reports that the PMS write either completed or permanently failed.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class GoTrackerAppointmentWritebackAction(str, Enum):
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"
    CONFIRM = "confirm"
    STATUS = "status"


class GoTrackerAppointmentWritebackStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class GoTrackerAppointmentWriteback(Base):
    """One ScaleNexus-originated appointment write pending PMS confirmation."""

    __tablename__ = "gotracker_appointment_writebacks"
    __table_args__ = (
        Index(
            "ix_gotracker_appt_writebacks_pending",
            "institution_id",
            "appointment_id",
            "status",
            "created_at",
        ),
        Index("ix_gotracker_appt_writebacks_run", "workflow_run_id"),
        CheckConstraint(
            "action IN ('reschedule', 'cancel', 'confirm', 'status')",
            name="ck_gotracker_appt_writebacks_action",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_gotracker_appt_writebacks_status",
        ),
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
        index=True,
    )
    appointment_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    step_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=GoTrackerAppointmentWritebackStatus.PENDING.value,
        server_default=text("'pending'"),
        index=True,
    )

    previous_start_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requested_start_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    preconfirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    completed_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    failed_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
