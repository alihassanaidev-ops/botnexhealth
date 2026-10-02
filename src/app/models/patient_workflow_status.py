"""Durable patient/campaign status events written by workflow action nodes."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class PatientWorkflowStatusEvent(Base):
    """Append-only local status trail for patient workflow outcomes.

    This table is intentionally local to ScaleNexus. It records that a campaign
    marked a contact/appointment as confirmed, no-answer, post-op complete, etc.
    without implying that we wrote those changes back to the PMS.
    """

    __tablename__ = "patient_workflow_status_events"
    __table_args__ = (
        Index(
            "ix_patient_workflow_status_events_contact",
            "institution_id",
            "contact_id",
            "created_at",
        ),
        Index(
            "ix_patient_workflow_status_events_run",
            "workflow_run_id",
            "created_at",
        ),
        Index(
            "ix_patient_workflow_status_events_status",
            "institution_id",
            "status",
            "created_at",
        ),
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
    contact_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="SET NULL"),
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
    workflow_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    step_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    trigger_ref_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    trigger_ref_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
