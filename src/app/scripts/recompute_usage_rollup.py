"""Recompute today's and yesterday's usage & cost rollup (Plan 11 M-2).

Designed to run on a 15-minute EventBridge → ECS schedule (same pattern as
``recompute_dashboard_rollup``). Each tick:

  1. Connects using ``DATABASE_ADMIN_URL`` — the runtime ``nexhealth_app`` role
     is NOBYPASSRLS, and the recompute touches every tenant's rows in one query,
     so it must run as a role that bypasses RLS.
  2. Calls :func:`src.app.services.usage_rollup.recompute_recent` which UPSERTs
     ``usage_cost_rollups`` rows for today and yesterday from ``usage_events``.
     Idempotent.
  3. Logs the row counts. Exits 0 on success, 1 on any failure so the scheduler
     can alarm.

Local invocation: ``python -m src.app.scripts.recompute_usage_rollup``.
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
from src.app.services.usage_rollup import recompute_recent

logger = logging.getLogger(__name__)


async def run() -> dict[str, int]:
    """Recompute today + yesterday and return the row-count summary."""
    admin_url = os.getenv("DATABASE_ADMIN_URL") or settings.database_url
    if not admin_url:
        raise SystemExit("DATABASE_URL/ADMIN_URL is not set; cannot recompute usage rollup")

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
        logger.exception("Usage rollup recompute failed")
        return 1
    logger.info("Usage rollup recompute complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
