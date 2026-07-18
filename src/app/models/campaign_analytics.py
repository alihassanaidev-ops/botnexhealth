"""Daily campaign outcome analytics rollups."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base
from src.app.models.usage_cost_rollup import NULL_LOCATION_SENTINEL


class CampaignMetricsDaily(Base):
    """One daily rollup row per workflow version.

    ``location_id`` uses the same all-zero sentinel as usage rollups when the
    source row has no location, keeping the primary key compact and non-null.
    """

    __tablename__ = "campaign_metrics_daily"
    __table_args__ = (
        PrimaryKeyConstraint(
            "institution_id",
            "location_id",
            "workflow_id",
            "workflow_version_id",
            "metric_date",
            name="pk_campaign_metrics_daily",
        ),
    )

    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, default=NULL_LOCATION_SENTINEL
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
    metric_date: Mapped[date_type] = mapped_column(Date, nullable=False)

    enrollments: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    active: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cancelled: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    suppressed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    sms_sent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sms_delivered: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sms_failed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sms_replied: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    voice_attempted: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    voice_answered: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    voice_voicemail: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    voice_failed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    email_sent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    email_delivered: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    email_opened: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    email_clicked: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    email_bounced: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    confirmed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    booked: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reschedule_requested: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    callback_requested: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    staff_handoff: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    opt_out: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    total_cost: Mapped[float] = mapped_column(Numeric(16, 5), nullable=False, default=0)
    cost_per_booking: Mapped[float | None] = mapped_column(Numeric(16, 5), nullable=True)
    cost_per_confirmation: Mapped[float | None] = mapped_column(Numeric(16, 5), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'USD'")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignOutcomeDefinition(Base):
    """Explicit labels and grouping for campaign-type-aware analytics."""

    __tablename__ = "campaign_outcome_definitions"
    __table_args__ = (
        UniqueConstraint(
            "category", "outcome_key", name="uq_campaign_outcome_definitions_category_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome_key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    group: Mapped[str] = mapped_column(String(24), nullable=False)
    description: Mapped[str | None] = mapped_column(String(240), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
