"""Recompute recent campaign outcome analytics rollups.

Designed for the same scheduled task pattern as usage/dashboard rollups. It uses
the admin DB URL because the rebuild scans every tenant's PHI-light operational
tables and writes tenant-scoped aggregate rows in one transaction.

Local invocation: ``python -m src.app.scripts.recompute_campaign_analytics``.
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
from src.app.services.automation.campaign_analytics_service import recompute_recent

logger = logging.getLogger(__name__)


async def run() -> dict[str, int]:
    admin_url = os.getenv("DATABASE_ADMIN_URL") or settings.database_url
    if not admin_url:
        raise SystemExit("DATABASE_URL/ADMIN_URL is not set; cannot recompute campaign analytics")

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
        logger.exception("Campaign analytics rollup recompute failed")
        return 1
    logger.info("Campaign analytics rollup recompute complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
