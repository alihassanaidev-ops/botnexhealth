"""Recompute today's and yesterday's dashboard rollup.

Designed to run on a 5-minute EventBridge → ECS schedule. Each tick:

  1. Connects to the database using ``DATABASE_ADMIN_URL`` (the
     runtime ``nexhealth_app`` role is NOBYPASSRLS — recompute
     touches every tenant's rows in one query, so it must run as a
     role that bypasses RLS).
  2. Calls :func:`src.app.services.dashboard_rollup.recompute_recent`
     which UPSERTs ``call_metrics_daily`` rows for today and
     yesterday from ``calls``. Idempotent.
  3. Logs the row counts (CloudWatch picks them up via the task's
     log driver). Exits 0 on success, 1 on any failure so the
     scheduler can alarm.

Local invocation is identical: ``python -m
src.app.scripts.recompute_dashboard_rollup``. Runs against whatever
DSN ``DATABASE_ADMIN_URL`` resolves to. See
``docs/SCHEDULED_JOBS.md`` for the full local debugging flow.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.app.config import settings
from src.app.services.dashboard_rollup import recompute_recent

logger = logging.getLogger(__name__)


async def run() -> dict[str, int]:
    """Recompute today + yesterday and return the row-count summary."""
    admin_url = os.getenv("DATABASE_ADMIN_URL") or settings.database_url
    if not admin_url:
        raise SystemExit(
            "DATABASE_URL/ADMIN_URL is not set; cannot recompute rollup"
        )

    # NullPool: this is a one-shot job and we don't want to leave a
    # connection pool sitting open in the scheduled task. The session
    # closes on context-exit and the engine is disposed below.
    engine = create_async_engine(admin_url, poolclass=NullPool)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with SessionFactory() as session:
            summary = await recompute_recent(session, today=date.today())
            await session.commit()
        return summary
    finally:
        await engine.dispose()


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        summary = asyncio.run(run())
    except Exception:
        logger.exception("Dashboard rollup recompute failed")
        return 1
    logger.info("Dashboard rollup recompute complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
