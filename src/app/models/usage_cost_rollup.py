"""Pre-aggregated daily usage & cost rollup (Plan 11 M-2).

One row per (institution_id, location_id, usage_date, channel, direction),
summarising ``usage_events`` so reporting reads are small SUMs over a few rows
per period instead of scanning raw per-interaction events. This is the contract
Plan 08 analytics depends on.

Owned by :mod:`src.app.services.usage_rollup` — ``recompute_window()`` rebuilds a
date range from ``usage_events`` via a single UPSERT-from-SELECT, mirroring
``dashboard_rollup``. Group/DSO totals are derived by summing an institution's
rows (join ``institutions.group_id``), not stored here.
"""

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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base

# Sentinel used when ``usage_events.location_id IS NULL`` so the rollup PK can
# stay NOT NULL. Same convention as call_metrics_daily.
NULL_LOCATION_SENTINEL = "00000000-0000-0000-0000-000000000000"


class UsageCostRollup(Base):
    """Daily aggregates of ``usage_events`` for fast cost/usage reporting."""

    __tablename__ = "usage_cost_rollups"
    __table_args__ = (
        PrimaryKeyConstraint(
            "institution_id",
            "location_id",
            "usage_date",
            "channel",
            "direction",
            name="pk_usage_cost_rollups",
        ),
    )

    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL location mapped to the all-zero sentinel so the PK column stays NOT NULL.
    location_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    usage_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)

    event_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_segments: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_dials: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_emails: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_minutes: Mapped[float] = mapped_column(
        Numeric(16, 4), nullable=False, default=0
    )
    total_cost_amount: Mapped[float] = mapped_column(
        Numeric(16, 5), nullable=False, default=0
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'USD'")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
