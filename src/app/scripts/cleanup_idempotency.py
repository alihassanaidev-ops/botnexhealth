"""Prune idempotency + dead-letter tables to bound their growth.

Three tables grow append-only:
  - ``retell_function_invocations`` — claim-then-finalize markers per
    Retell function call. Idempotency only matters within Retell's own
    retry window (minutes); rows older than that are dead weight.
  - ``retell_webhook_events`` — same pattern for inbound webhooks.
  - ``dead_letter_events`` — failed event payloads. Useful for
    forensics for a few weeks; older entries get archived/dropped.

At 1k tenants × ~100 calls/day each, ``retell_function_invocations``
adds ~36M rows/year if never pruned. A periodic ``DELETE WHERE
created_at < cutoff`` keeps the working set bounded.

Designed to be run as a scheduled Fargate task (CloudWatch
EventBridge → ECS), or locally as a one-shot. The script connects to
the database using the same DSN the rest of the app uses, batches
deletes to avoid long locks, and exits non-zero on any failure so
the scheduler can alarm.

Idempotent: re-running has no effect once the cutoff window is empty.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.app.config import settings

logger = logging.getLogger(__name__)


# Default retention windows. The Retell idempotency tables only need to
# guard against retries within Retell's own delivery window (minutes,
# stretched generously to a day to cover backlog + clock skew). The
# dead-letter table is ops forensic data, so it lives longer by default.
_DEFAULT_RETENTION_DAYS: dict[str, int] = {
    "retell_function_invocations": 30,
    "retell_webhook_events": 30,
    "dead_letter_events": 90,
}

# Delete in batches to avoid long write locks on a large table.
_BATCH_SIZE = 5000


def _retention_days(table: str) -> int:
    env_var = f"CLEANUP_RETENTION_DAYS_{table.upper()}"
    raw = os.getenv(env_var)
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.warning(
                "Invalid %s=%r, falling back to default %d",
                env_var,
                raw,
                _DEFAULT_RETENTION_DAYS[table],
            )
    return _DEFAULT_RETENTION_DAYS[table]


async def _delete_old_rows(
    engine: Any,
    table: str,
    cutoff: datetime,
) -> int:
    """Delete rows older than ``cutoff`` in batches; return total deleted.

    Uses ``ctid``-anchored batches so each statement holds locks only on
    the rows it's deleting — much friendlier to concurrent writers than
    a single unbounded ``DELETE``.
    """
    total_deleted = 0
    while True:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    f"""
                    DELETE FROM {table}
                    WHERE ctid IN (
                        SELECT ctid
                        FROM {table}
                        WHERE created_at < :cutoff
                        ORDER BY created_at
                        LIMIT :batch_size
                    )
                    """
                ),
                {"cutoff": cutoff, "batch_size": _BATCH_SIZE},
            )
            deleted = result.rowcount or 0
        total_deleted += deleted
        if deleted < _BATCH_SIZE:
            break
    return total_deleted


async def run() -> dict[str, int]:
    """Prune all configured tables. Returns a per-table delete count.

    Connects via ``DATABASE_ADMIN_URL`` when available — the runtime
    ``nexhealth_app`` role is NOBYPASSRLS and the cleanup is an
    admin-level cross-tenant operation. Falling back to
    ``DATABASE_URL`` only makes sense in local dev where one role
    does both jobs.
    """
    admin_url = os.getenv("DATABASE_ADMIN_URL") or settings.database_url
    if not admin_url:
        raise SystemExit("DATABASE_URL/ADMIN_URL is not set; cannot run cleanup")

    # NullPool — this is a one-shot job and we don't want to leave a
    # pool sitting open in the scheduled task.
    engine = create_async_engine(admin_url, poolclass=NullPool)
    summary: dict[str, int] = {}
    try:
        for table in _DEFAULT_RETENTION_DAYS:
            days = _retention_days(table)
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            logger.info(
                "Pruning %s rows older than %s (%d days)", table, cutoff, days
            )
            deleted = await _delete_old_rows(engine, table, cutoff)
            summary[table] = deleted
            logger.info("Pruned %d rows from %s", deleted, table)
    finally:
        await engine.dispose()

    return summary


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    try:
        summary = asyncio.run(run())
    except Exception:
        logger.exception("Idempotency cleanup failed")
        return 1
    logger.info("Idempotency cleanup complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
