"""Call model for storing post-call records.

HIPAA Compliance:
- All records are institution-scoped (institution_id NOT NULL).
- Transcript and summary are AES-256-GCM encrypted at the application
  layer (defense in depth on top of RDS at-rest encryption).
- We only persist Retell's scrubbed outputs — raw, unredacted transcripts
  never reach this table.
- retell_call_id UNIQUE constraint ensures webhook idempotency.
"""

from __future__ import annotations

import json
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
    text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


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
        # Location-scoped dashboard queries filter by institution, agent, and date.
        Index("ix_call_institution_agent_date", "institution_id", "agent_used", "call_date"),
        # Dashboard callback queue only needs unresolved needs-callback rows.
        Index(
            "ix_call_dashboard_open_callbacks",
            "institution_id",
            "call_date",
            "created_at",
            postgresql_where=text(
                "call_status = 'needs_callback' AND callback_resolved = false"
            ),
        ),
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

    # Call content (AES-256-GCM encrypted — see encrypt_value/decrypt_value)
    # Only Retell's scrubbed structured transcript is stored. Raw transcripts
    # never reach this table.
    transcript_with_tool_calls_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # =========================================================================
    # Encrypted field properties
    # =========================================================================

    @property
    def transcript_with_tool_calls(self) -> list | None:
        """Decrypted scrubbed structured transcript (turn-by-turn)."""
        raw = decrypt_value(self.transcript_with_tool_calls_encrypted)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None

    @transcript_with_tool_calls.setter
    def transcript_with_tool_calls(self, value: list | None) -> None:
        if value is None:
            self.transcript_with_tool_calls_encrypted = None
            return
        self.transcript_with_tool_calls_encrypted = encrypt_value(
            json.dumps(value, separators=(",", ":"), default=str)
        )

    @property
    def summary(self) -> str | None:
        return decrypt_value(self.summary_encrypted)

    @summary.setter
    def summary(self, value: str | None) -> None:
        self.summary_encrypted = encrypt_value(value)

    def __repr__(self) -> str:
        return (
            f"<Call(id={self.id}, institution_id={self.institution_id}, "
            f"status={self.call_status}, direction={self.call_direction})>"
        )
