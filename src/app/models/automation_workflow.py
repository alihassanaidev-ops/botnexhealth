"""Outbound automation workflow engine schema.

These models are the durable foundation for outbound campaign enrollment and
execution. They intentionally do not send messages; channel delivery and
compliance policy live in separate subsystems.
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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database import Base


class AutomationWorkflowStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class AutomationRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"


class AutomationStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    BLOCKED = "blocked"


class AutomationTimerStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    FIRED = "fired"
    CANCELLED = "cancelled"


class AutomationWorkflow(Base):
    """A tenant-authored automation workflow definition."""

    __tablename__ = "automation_workflows"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'archived')",
            name="ck_automation_workflows_status",
        ),
        Index("ix_automation_workflows_institution_status", "institution_id", "status"),
        Index("ix_automation_workflows_location_status", "location_id", "status"),
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
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    current_version_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey(
            "automation_workflow_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_automation_workflows_current_version",
        ),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=AutomationWorkflowStatus.DRAFT.value,
        server_default=text("'draft'"),
        nullable=False,
    )
    is_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    versions: Mapped[list["AutomationWorkflowVersion"]] = relationship(
        "AutomationWorkflowVersion",
        back_populates="workflow",
        foreign_keys="AutomationWorkflowVersion.workflow_id",
        lazy="selectin",
    )
    current_version: Mapped["AutomationWorkflowVersion | None"] = relationship(
        "AutomationWorkflowVersion",
        foreign_keys=[current_version_id],
        lazy="selectin",
    )


class AutomationWorkflowVersion(Base):
    """Immutable workflow definition snapshot used by runs."""

    __tablename__ = "automation_workflow_versions"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "version_number",
            name="uq_automation_workflow_versions_workflow_number",
        ),
        Index("ix_automation_workflow_versions_institution", "institution_id"),
        Index("ix_automation_workflow_versions_workflow", "workflow_id"),
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
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    definition_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_classification: Mapped[str | None] = mapped_column(String(50), nullable=True)
    published_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    workflow: Mapped[AutomationWorkflow] = relationship(
        "AutomationWorkflow",
        back_populates="versions",
        foreign_keys=[workflow_id],
        lazy="selectin",
    )


class AutomationWorkflowRun(Base):
    """A single contact enrollment through one immutable workflow version."""

    __tablename__ = "automation_workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting', 'completed', 'cancelled', "
            "'failed', 'blocked')",
            name="ck_automation_workflow_runs_status",
        ),
        Index("ix_automation_workflow_runs_institution_status", "institution_id", "status"),
        Index("ix_automation_workflow_runs_location_status", "location_id", "status"),
        Index("ix_automation_workflow_runs_contact", "institution_id", "contact_id"),
        Index("ix_automation_workflow_runs_workflow", "workflow_id", "created_at"),
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
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trigger_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    trigger_ref_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    trigger_ref_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    trigger_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=AutomationRunStatus.PENDING.value,
        server_default=text("'pending'"),
        nullable=False,
    )
    current_step_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(80), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class AutomationWorkflowStepExecution(Base):
    """Attempt-level state for one workflow step inside a run."""

    __tablename__ = "automation_workflow_step_executions"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "step_id",
            "attempt_number",
            name="uq_automation_step_execution_attempt",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting', 'completed', 'skipped', "
            "'failed', 'blocked')",
            name="ck_automation_workflow_step_executions_status",
        ),
        Index("ix_automation_step_executions_run", "workflow_run_id", "step_id"),
        Index("ix_automation_step_executions_status", "institution_id", "status"),
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
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    workflow_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )

    step_id: Mapped[str] = mapped_column(String(120), nullable=False)
    step_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=AutomationStepStatus.PENDING.value,
        server_default=text("'pending'"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_local_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    scheduled_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class AutomationWorkflowTimer(Base):
    """Durable scheduler row for delayed workflow execution."""

    __tablename__ = "automation_workflow_timers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'claimed', 'fired', 'cancelled')",
            name="ck_automation_workflow_timers_status",
        ),
        Index("ix_automation_workflow_timers_due", "status", "due_at"),
        Index("ix_automation_workflow_timers_run", "workflow_run_id"),
        Index("ix_automation_workflow_timers_institution_status", "institution_id", "status"),
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
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_execution_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_step_executions.id", ondelete="CASCADE"),
        nullable=True,
    )

    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    due_local_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=AutomationTimerStatus.PENDING.value,
        server_default=text("'pending'"),
        nullable=False,
    )
    claim_token: Mapped[str | None] = mapped_column(String(120), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class AutomationWorkflowEvent(Base):
    """Append-style workflow audit/event stream without raw PHI payloads."""

    __tablename__ = "automation_workflow_events"
    __table_args__ = (
        Index("ix_automation_workflow_events_run", "workflow_run_id", "occurred_at"),
        Index("ix_automation_workflow_events_institution", "institution_id", "occurred_at"),
        Index("ix_automation_workflow_events_type", "institution_id", "event_type"),
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
    location_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    workflow_version_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    step_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
