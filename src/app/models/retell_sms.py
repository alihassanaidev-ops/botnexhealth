"""Durable state for Retell-generated, platform-transported SMS conversations."""

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


class RetellSmsSessionStatus(str, Enum):
    AWAITING_USER = "awaiting_user"
    GENERATING = "generating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    HANDOFF = "handoff"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    OPTED_OUT = "opted_out"


class RetellSmsTurnStatus(str, Enum):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


ACTIVE_RETELL_SMS_SESSION_STATUSES: tuple[str, ...] = (
    RetellSmsSessionStatus.AWAITING_USER.value,
    RetellSmsSessionStatus.GENERATING.value,
)


class RetellSmsChatProfile(Base):
    """Named per-location Retell Chat configuration selected by workflow nodes."""

    __tablename__ = "retell_sms_chat_profiles"
    __table_args__ = (
        Index(
            "uq_retell_sms_profiles_active_location_purpose",
            "location_id",
            "purpose",
            unique=True,
            postgresql_where=text("is_active = true AND purpose IS NOT NULL"),
        ),
        Index(
            "uq_retell_sms_profiles_active_agent",
            "retell_agent_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index(
            "ix_retell_sms_profiles_institution_active", "institution_id", "is_active"
        ),
        Index("ix_retell_sms_profiles_location_active", "location_id", "is_active"),
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
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    retell_agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(80), nullable=True)
    allowed_tools: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RetellSmsSession(Base):
    """Local authority for one run-scoped AI SMS conversation.

    ``retell_chat_id`` is a vendor correlation id, created on the first inbound
    turn. Local expiry remains authoritative even if Retell still reports the
    corresponding chat as ongoing.
    """

    __tablename__ = "retell_sms_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_user', 'generating', 'completed', 'cancelled', "
            "'handoff', 'timed_out', 'failed', 'opted_out')",
            name="ck_retell_sms_sessions_status",
        ),
        Index(
            "uq_retell_sms_sessions_active_contact_location",
            "institution_id",
            "location_id",
            "contact_id",
            unique=True,
            postgresql_where=text("status IN ('awaiting_user', 'generating')"),
        ),
        Index(
            "uq_retell_sms_sessions_run_step",
            "workflow_run_id",
            "step_id",
            unique=True,
        ),
        Index(
            "uq_retell_sms_sessions_chat_id",
            "retell_chat_id",
            unique=True,
            postgresql_where=text("retell_chat_id IS NOT NULL"),
        ),
        Index("ix_retell_sms_sessions_thread", "conversation_thread_id"),
        Index("ix_retell_sms_sessions_expiry", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
    )
    contact_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_execution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_step_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_thread_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("campaign_conversation_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_profile_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("retell_sms_chat_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    step_id: Mapped[str] = mapped_column(String(120), nullable=False)
    retell_chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retell_agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=RetellSmsSessionStatus.AWAITING_USER.value,
        server_default=text("'awaiting_user'"),
    )
    turn_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    max_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    terminal_outcome: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RetellSmsTurn(Base):
    """Idempotency and processing ledger for one inbound patient SMS turn."""

    __tablename__ = "retell_sms_turns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('claimed', 'completed', 'failed', 'ambiguous')",
            name="ck_retell_sms_turns_status",
        ),
        Index(
            "uq_retell_sms_turns_message_sid",
            "message_sid",
            unique=True,
            postgresql_where=text("message_sid IS NOT NULL"),
        ),
        Index("uq_retell_sms_turns_inbound", "inbound_sms_message_id", unique=True),
        Index("ix_retell_sms_turns_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("retell_sms_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    inbound_sms_message_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("inbound_sms_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=RetellSmsTurnStatus.CLAIMED.value,
        server_default=text("'claimed'"),
    )
    retell_message_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    outbound_sms_history_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("sms_history_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
