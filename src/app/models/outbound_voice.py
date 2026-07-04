"""Outbound voice data model (Plan 03 / register V-4).

Gives outbound campaign voice its own durable system of record, distinct from the
generic step-execution ledger:

- ``outbound_voice_profiles`` — per-location outbound configuration (which Retell
  agent + from-number a location dials with, plus free-form config). The voice
  executor resolves it as an *override with fallback* to the node/location
  defaults, so an absent profile changes nothing (backward-compatible).
- ``workflow_voice_attempts`` — one row per placed outbound call attempt, carrying
  the ``retell_call_id`` correlation key, masked endpoints, a lifecycle ``status``,
  and the normalized ``dial_outcome``. This is the attempt/outcome history the UI
  drills into (V-8) and the substrate for the crash-safe committed claim (P9).

Both tables carry ``institution_id``/``location_id`` and are RLS-scoped exactly
like the other automation tables (see the migration).
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class VoiceAttemptStatus(str, Enum):
    """Lifecycle of a single outbound voice attempt row.

    ``INITIATING`` is the committed-before-POST claim state (reserved for P9);
    the executor today creates the row already ``PLACED`` (fire-and-forget) or
    ``AWAITING_OUTCOME`` (wait-for-outcome), then the post-call webhook resolves
    it to ``COMPLETED``. ``FAILED`` records a placement error.
    """

    INITIATING = "initiating"
    PLACED = "placed"
    AWAITING_OUTCOME = "awaiting_outcome"
    COMPLETED = "completed"
    FAILED = "failed"


# Normalized dial outcomes (mirrors src/app/services/automation/voice_outcome.py).
# Kept in sync with the webhook mapper; NULL until the post-call webhook resolves.
VOICE_DIAL_OUTCOMES: tuple[str, ...] = (
    "no_answer",
    "busy",
    "voicemail",
    "answered",
    "transferred",
    "failed",
    "unknown",
)


class OutboundVoiceProfile(Base):
    """Per-location outbound voice configuration.

    Formalizes what the executor otherwise reads from the node
    (``retell_agent_id``) and the location (``retell_from_number``): a location can
    declare its own outbound agent/number here. Resolution is override-with-fallback
    — an absent or inactive profile leaves existing behavior unchanged. At most one
    active profile per location (partial unique index).
    """

    __tablename__ = "outbound_voice_profiles"
    __table_args__ = (
        Index(
            "uq_outbound_voice_profiles_active_location",
            "location_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index("ix_outbound_voice_profiles_institution_active", "institution_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institution_locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    retell_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retell_from_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retell_llm_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkflowVoiceAttempt(Base):
    """One placed outbound voice-call attempt inside a workflow run.

    Correlates the Retell call back to the run via ``retell_call_id`` (unique when
    present) and records the normalized dial outcome once the post-call webhook
    lands. Links to the specific step-execution attempt so the UI can drill from a
    run into its call history. No raw phone numbers — only masked forms (PHI-safe).
    """

    __tablename__ = "workflow_voice_attempts"
    __table_args__ = (
        Index(
            "uq_workflow_voice_attempts_retell_call_id",
            "retell_call_id",
            unique=True,
            postgresql_where=text("retell_call_id IS NOT NULL"),
        ),
        Index("ix_workflow_voice_attempts_run", "workflow_run_id"),
        Index("ix_workflow_voice_attempts_institution_status", "institution_id", "status"),
        CheckConstraint(
            "status IN ('initiating', 'placed', 'awaiting_outcome', 'completed', 'failed')",
            name="ck_workflow_voice_attempts_status",
        ),
        CheckConstraint(
            "dial_outcome IS NULL OR dial_outcome IN "
            "('no_answer', 'busy', 'voicemail', 'answered', 'transferred', 'failed', 'unknown')",
            name="ck_workflow_voice_attempts_dial_outcome",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("institution_locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("automation_workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    step_execution_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_step_executions.id", ondelete="SET NULL"),
        nullable=True,
    )
    step_id: Mapped[str] = mapped_column(String(120), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    retell_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_number_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_number_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    dial_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    disconnection_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
