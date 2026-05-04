"""Pre-aggregated daily call metrics for the dashboard.

Live aggregate queries against ``calls`` (8x ``COUNT FILTER``, GROUP BY
``call_status``, AVG duration) are fine until the table grows past
~100k rows per institution. Past that point the aggregate scans pin
DB CPU under any concurrent admin traffic.

This table holds one row per (institution_id, location_id, call_date).
Dashboard queries for "this week", "this month", "all time" become
small SUMs over a few hundred rows; today's data still goes live, since
the rollup is recomputed on a schedule and lags slightly.

The rollup is owned by :mod:`src.app.services.dashboard_rollup` —
recompute_window() rebuilds a date range from ``calls`` via UPSERT.
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
    PrimaryKeyConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database import Base


# Sentinel UUID used in the rollup table when ``calls.location_id IS NULL``.
# An all-zero UUID is a safe choice: it never collides with a real location
# (which uses uuid4) and makes the rollup row easy to spot in queries.
NULL_LOCATION_SENTINEL = "00000000-0000-0000-0000-000000000000"


class CallMetricsDaily(Base):
    """Daily aggregates of ``calls`` rows for fast dashboard reads."""

    __tablename__ = "call_metrics_daily"
    __table_args__ = (
        PrimaryKeyConstraint(
            "institution_id",
            "location_id",
            "call_date",
            name="pk_call_metrics_daily",
        ),
    )

    institution_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("institutions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ``calls.location_id`` is nullable; the recompute maps NULL to the
    # all-zero sentinel so this PK column can stay NOT NULL.
    location_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
    )
    call_date: Mapped[date_type] = mapped_column(Date, nullable=False)

    total_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_patient_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    complaint_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    insurance_billing_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    total_duration_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    # Per-status counts as JSONB so adding a new CallStatus enum value
    # doesn't require a schema migration. Shape: {"<status>": <count>, ...}.
    tag_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
