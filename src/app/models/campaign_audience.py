"""Saved campaign audience definitions and short-lived preview metadata."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


class CampaignAudienceDefinition(Base):
    """Persisted constrained segment DSL for one workflow."""

    __tablename__ = "campaign_audience_definitions"
    __table_args__ = (
        UniqueConstraint("workflow_id", name="uq_campaign_audience_definitions_workflow"),
        Index("ix_campaign_audience_definitions_institution", "institution_id"),
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
        UUID(as_uuid=False), ForeignKey("institution_locations.id", ondelete="SET NULL"), nullable=True
    )
    workflow_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    segment: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    exclusions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CampaignAudiencePreview(Base):
    """PHI-light preview summary retained briefly for idempotent enroll commits."""

    __tablename__ = "campaign_audience_previews"
    __table_args__ = (
        Index("ix_campaign_audience_previews_workflow_created", "workflow_id", "created_at"),
        Index("ix_campaign_audience_previews_expires", "expires_at"),
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
        UUID(as_uuid=False), ForeignKey("institution_locations.id", ondelete="SET NULL"), nullable=True
    )
    workflow_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_version_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("automation_workflow_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    segment: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    exclusions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    counts_by_reason: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    included_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
