"""Call model for storing post-call records.

HIPAA Compliance:
- All records are institution-scoped (institution_id NOT NULL).
- Transcript and summary may contain PHI references — stored in institution-isolated rows.
- retell_call_id UNIQUE constraint ensures webhook idempotency.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database import Base


class CallStatus(str, Enum):
    """Primary outcome tag for a call (first normalized tag from Retell 'Call Status' field)."""

    APPOINTMENT_BOOKED = "appointment_booked"
    APPOINTMENT_RESCHEDULED = "appointment_rescheduled"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    EMERGENCY = "emergency"
    COMPLAINT = "complaint"
    NEEDS_CALLBACK = "needs_callback"
    FAQ_HANDLED = "faq_handled"
    FINANCIAL_INQUIRY = "financial_inquiry"
    TRANSFERRED = "transferred"
    INSURANCE_VERIFIED = "insurance_verified"
    INSURANCE_UNVERIFIED = "insurance_unverified"
    NO_ACTION_NEEDED = "no_action_needed"


class PatientStatus(str, Enum):
    """Whether the patient has been contacted/followed up."""

    CONTACTED = "contacted"
    NOT_CONTACTED = "not_contacted"


class CallDirection(str, Enum):
    """Direction of the call."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class Call(Base):
    """
    A single call record linked to a contact and institution.

    Created during post-call webhook processing. The retell_call_id
    serves as an idempotency key to prevent duplicate records.
    """

    __tablename__ = "calls"
    __table_args__ = (
        Index("ix_call_institution", "institution_id"),
        Index("ix_call_institution_status", "institution_id", "call_status"),
        Index("ix_call_institution_date", "institution_id", "call_date"),
        Index("ix_call_institution_contact", "institution_id", "contact_id"),
    )

    # Primary key
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    # Institution isolation
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Contact reference (nullable for unknown callers)
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Idempotency key — prevents duplicate records from webhook retries
    retell_call_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True, index=True,
    )

    # Call metadata
    call_direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    agent_used: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Call content
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured JSONB transcripts from Retell (turn-by-turn with tool calls)
    # transcript_with_tool_calls: unredacted full conversation (internal use only)
    # scrubbed_transcript_with_tool_calls: PII-scrubbed version (shown in dashboard by default)
    transcript_with_tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    scrubbed_transcript_with_tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Classification
    patient_sentiment: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Primary status (first normalized tag) — indexed for fast single-tag filtering
    call_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    # All tags comma-separated e.g. "complaint,faq_handled" — for multi-tag display & filter
    call_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    patient_status: Mapped[str | None] = mapped_column(
        String(50), default=PatientStatus.NOT_CONTACTED.value, nullable=True,
    )
    patient_intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timing
    call_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    call_time: Mapped[time | None] = mapped_column(Time(timezone=True), nullable=True)
    call_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Patient flags
    is_new_patient: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_complaint: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_insurance_billing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Callback tracking
    preferred_callback_datetime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    callback_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    callback_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    callback_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metrics
    times_called: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Timestamps
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

    # Relationships
    contact: Mapped["Contact"] = relationship(  # noqa: F821
        "Contact", back_populates="calls", lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<Call(id={self.id}, institution_id={self.institution_id}, "
            f"status={self.call_status}, direction={self.call_direction})>"
        )
