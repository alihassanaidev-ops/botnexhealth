"""Normalized campaign patient responses and staff handoffs."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.institution import decrypt_value, encrypt_value


class CampaignResponseEvent(Base):
    """PHI-light normalized audit trail for patient campaign responses."""

    __tablename__ = "campaign_response_events"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('sms', 'voice', 'email', 'booking_link', 'staff')",
            name="ck_campaign_response_events_channel",
        ),
        Index(
            "ix_campaign_response_events_institution_created",
            "institution_id",
            "occurred_at",
        ),
        Index("ix_campaign_response_events_run_created", "workflow_run_id", "occurred_at"),
        Index("ix_campaign_response_events_workflow", "workflow_id", "occurred_at"),
        Index("ix_campaign_response_events_contact", "contact_id"),
        Index("ix_campaign_response_events_source", "institution_id", "source_event_id"),
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
    )
    workflow_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    normalized_intent: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_outcome: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_event_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="deterministic")
    summary: Mapped[str | None] = mapped_column(String(240), nullable=True)

    raw_body_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def raw_body(self) -> str | None:
        return decrypt_value(self.raw_body_encrypted)

    @raw_body.setter
    def raw_body(self, value: str | None) -> None:
        self.raw_body_encrypted = encrypt_value(value)

    @property
    def raw_payload(self) -> dict | None:
        raw = decrypt_value(self.raw_payload_encrypted)
        if not raw:
            return None
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else None

    @raw_payload.setter
    def raw_payload(self, value: dict | None) -> None:
        if value is None:
            self.raw_payload_encrypted = None
            return
        self.raw_payload_encrypted = encrypt_value(
            json.dumps(value, separators=(",", ":"), sort_keys=True)
        )


class CampaignStaffHandoff(Base):
    """Human follow-up item created when automation should not decide."""

    __tablename__ = "campaign_staff_handoffs"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('free_text', 'reschedule_requested', 'cancel_requested', "
            "'clinical_question', 'billing_question', 'automation_failed', "
            "'ambiguous_response', 'ambiguous_voice_outcome', 'patient_asks_for_staff', "
            "'failed_booking')",
            name="ck_campaign_staff_handoffs_reason",
        ),
        CheckConstraint(
            "status IN ('open', 'assigned', 'resolved', 'dismissed')",
            name="ck_campaign_staff_handoffs_status",
        ),
        Index("ix_campaign_staff_handoffs_institution_status", "institution_id", "status"),
        Index("ix_campaign_staff_handoffs_run_created", "workflow_run_id", "created_at"),
        Index("ix_campaign_staff_handoffs_workflow_status", "workflow_id", "status"),
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
    )
    workflow_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    response_event_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("campaign_response_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    assignee_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    summary: Mapped[str | None] = mapped_column(String(240), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_outcome: Mapped[str | None] = mapped_column(String(80), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
