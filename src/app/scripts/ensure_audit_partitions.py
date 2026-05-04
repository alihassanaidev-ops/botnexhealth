"""Maintain ``audit_logs`` monthly partitions.

The partitioning migration creates a small initial window (current
month + 6 forward + previous + DEFAULT). After deploy this script runs
daily to keep the rolling window alive: as time advances, it ensures
N future months are always pre-created so an INSERT for next month
never falls through to the DEFAULT partition.

Idempotent. ``CREATE TABLE IF NOT EXISTS`` means re-running is a
no-op once the window is full. Safe to re-run by hand at any time.

Connects via ``DATABASE_ADMIN_URL`` (master role); partition DDL
requires ownership of the parent table. Logs created vs. already-
existing partitions for operator visibility.

Designed to run as an EventBridge → ECS scheduled Fargate task,
matching the existing scheduled-job pattern (cleanup_idempotency,
recompute_dashboard_rollup).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.app.config import settings

logger = logging.getLogger(__name__)


# How many months ahead to keep partitioned. Safe upper bound — daily
# runs only do work on the months not already covered, so a generous
# window costs nothing in steady state.
DEFAULT_FUTURE_MONTHS = 6


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    new_index = month - 1 + delta
    return year + new_index // 12, (new_index % 12) + 1


def _partition_name(year: int, month: int) -> str:
    return f"audit_logs_y{year}_m{month:02d}"


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    next_year, next_month = _add_months(year, month, 1)
    return date(year, month, 1), date(next_year, next_month, 1)


async def _ensure_partition(conn, year: int, month: int) -> bool:
    """Create the partition for the given (year, month) if missing.

    Returns True if a partition was actually created, False if it
    already existed.
    """
    name = _partition_name(year, month)
    start, end = _month_bounds(year, month)

    # The CREATE TABLE statement is intentionally split out from a
    # check-then-create racy pattern: we use the IF NOT EXISTS form so
    # parallel runs (an EventBridge tick overlapping with a manual
    # operator run) are safe.
    existed = await conn.scalar(
        text(
            "SELECT 1 FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = :name"
        ),
        {"name": name},
    )

    await conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {name} "
            f"PARTITION OF audit_logs "
            f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
        )
    )

    if not existed:
        # Grant DML to the runtime role on the new partition. Parent-
        # level GRANTs do NOT propagate to partitions in PostgreSQL —
        # without this, a fresh partition would 403 ``nexhealth_app``
        # writes the moment ``timestamp >= start_date``.
        await conn.execute(
            text(
                f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') "
                f"THEN GRANT SELECT, INSERT ON {name} TO nexhealth_app; END IF; END $$"
            )
        )
        logger.info("Created audit partition %s [%s, %s)", name, start, end)
        return True
    return False


async def run(future_months: int = DEFAULT_FUTURE_MONTHS) -> dict[str, int]:
    """Ensure the rolling audit-partition window is intact.

    Returns a summary like ``{"created": 1, "already_present": 6}``.
    """
    admin_url = os.getenv("DATABASE_ADMIN_URL") or settings.database_url
    if not admin_url:
        raise SystemExit(
            "DATABASE_URL/ADMIN_URL is not set; cannot ensure audit partitions"
        )

    engine = create_async_engine(admin_url, poolclass=NullPool)
    today = date.today()

    created = 0
    already_present = 0
    try:
        # Single transaction: if any partition fails (e.g., DDL lock
        # contention), the whole tick rolls back and the next run
        # picks it up. Keeps the rolling window consistent.
        async with engine.begin() as conn:
            for offset in range(future_months + 1):
                year, month = _add_months(today.year, today.month, offset)
                if await _ensure_partition(conn, year, month):
                    created += 1
                else:
                    already_present += 1
    finally:
        await engine.dispose()

    summary = {"created": created, "already_present": already_present}
    logger.info("Audit partition maintenance complete: %s", summary)
    return summary


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except Exception:
        logger.exception("Audit partition maintenance failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
