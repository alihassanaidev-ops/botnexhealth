"""Run-scoped campaign conversation threads."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class CampaignConversationThread(Base):
    """One patient conversation thread scoped to one workflow run."""

    __tablename__ = "campaign_conversation_threads"
    __table_args__ = (
        CheckConstraint("channel IN ('sms', 'email')", name="ck_campaign_conversation_threads_channel"),
        CheckConstraint(
            "status IN ('open', 'completed', 'handoff')",
            name="ck_campaign_conversation_threads_status",
        ),
        Index(
            "ix_campaign_conversation_threads_lookup",
            "institution_id",
            "location_id",
            "contact_id",
            "channel",
            "status",
        ),
        Index(
            "uq_campaign_conversation_threads_active_run_channel",
            "workflow_run_id",
            "channel",
            unique=True,
            postgresql_where=text("status IN ('open', 'handoff')"),
        ),
        Index(
            "uq_campaign_conversation_threads_active_cold_email",
            "institution_id",
            "location_id",
            "contact_id",
            "channel",
            unique=True,
            postgresql_where=text(
                "workflow_run_id IS NULL AND channel = 'email' "
                "AND status IN ('open', 'handoff')"
            ),
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
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institution_locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflows.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(24), nullable=False, default="sms")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="open", server_default=text("'open'"), index=True
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
