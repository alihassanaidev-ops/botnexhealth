"""When each scheduled campaign next fires, per location.

Celery beat is static, hardcoded, and runs embedded in a worker pinned to one
instance — a second would double-fire every entry — so per-campaign schedules
cannot be beat entries. They are rows, claimed by one fixed-interval beat task
the same way ``AutomationWorkflowTimer`` rows are, which inherits that path's
``FOR UPDATE SKIP LOCKED`` claiming and per-institution fairness.

One row per (workflow, location): "every weekday at 9am" means each clinic's own
9am, so a two-site campaign has two rows with different ``next_fire_at``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class WorkflowSchedule(Base):
    """A campaign's next due tick at one location."""

    __tablename__ = "workflow_schedules"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "location_id",
            name="uq_workflow_schedules_workflow_location",
        ),
        # The claim query: due rows, soonest first.
        Index("ix_workflow_schedules_due", "is_active", "next_fire_at"),
        Index("ix_workflow_schedules_institution", "institution_id", "is_active"),
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
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_versions.id", ondelete="CASCADE"),
        nullable=False,
    )

    cron: Mapped[str] = mapped_column(String(120), nullable=False)
    #: IANA zone the cron is read in — the location's own, unless the campaign
    #: pinned one.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    next_fire_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    #: The cursor that stops a beat outage from replaying every missed slot and
    #: a DST shift from firing the same slot twice.
    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Paused/archived campaigns keep their row so resuming does not lose the
    #: cursor, but are skipped by the claim.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
