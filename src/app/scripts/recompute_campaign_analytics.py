"""Recompute campaign outcome analytics rollups.

Designed for the same scheduled task pattern as usage/dashboard rollups. It uses
the admin DB URL because the rebuild scans every tenant's PHI-light operational
tables and writes tenant-scoped aggregate rows in one transaction.

With no arguments it rebuilds the recent window, which is what the scheduled task
wants. ``--start``/``--end`` rebuild an explicit range instead: a metric column
added after the fact holds a server-default zero for every historical day until
that day is recomputed, and a zero is indistinguishable from a real one on the
reporting screen.

Local invocation: ``python -m src.app.scripts.recompute_campaign_analytics``, or
``... --start 2026-01-01 --end 2026-09-02`` to backfill.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.app.config import settings
from src.app.services.automation.campaign_analytics_service import (
    recompute_recent,
    recompute_window,
)

logger = logging.getLogger(__name__)


async def run(
    *, start_date: date | None = None, end_date: date | None = None
) -> dict[str, int]:
    admin_url = os.getenv("DATABASE_ADMIN_URL") or settings.database_url
    if not admin_url:
        raise SystemExit("DATABASE_URL/ADMIN_URL is not set; cannot recompute campaign analytics")

    engine = create_async_engine(admin_url, poolclass=NullPool)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with SessionFactory() as session:
            if start_date is None and end_date is None:
                summary = await recompute_recent(session, today=date.today())
            else:
                summary = await recompute_window(
                    session,
                    start_date=start_date or end_date,
                    end_date=end_date or date.today(),
                )
            await session.commit()
        return summary
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=None,
        help="Inclusive first day to rebuild (YYYY-MM-DD). Defaults to --end.",
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="Inclusive last day to rebuild (YYYY-MM-DD). Defaults to today.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    try:
        summary = asyncio.run(run(start_date=args.start, end_date=args.end))
    except Exception:
        logger.exception("Campaign analytics rollup recompute failed")
        return 1
    logger.info("Campaign analytics rollup recompute complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
