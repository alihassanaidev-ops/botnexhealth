"""Run explicit database preparation and Alembic migrations."""

from __future__ import annotations

import asyncio
import logging
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.app.config import settings
from src.app.scripts.bootstrap_database import (
    _ALEMBIC_VERSION_TABLE,
    _list_tables,
    ensure_database_bootstrapped,
)

logger = logging.getLogger(__name__)

_BASELINE_ENV_VAR = "ALEMBIC_BASELINE_REVISION"


async def _get_alembic_revisions(database_url: str) -> list[str]:
    table_names = await _list_tables(database_url)
    if _ALEMBIC_VERSION_TABLE not in table_names:
        return []

    engine = create_async_engine(database_url, echo=False, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num"))
            return [row[0] for row in result]
    finally:
        await engine.dispose()


def _alembic_config() -> Config:
    return Config("alembic.ini")


def _stamp_revision(revision: str) -> None:
    command.stamp(_alembic_config(), revision)


def _upgrade_head() -> None:
    command.upgrade(_alembic_config(), "head")


def main() -> int:
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set.")

    bootstrapped = ensure_database_bootstrapped(settings.database_url)
    if bootstrapped:
        logger.warning("Database was empty. Bootstrapped schema and stamped Alembic head.")
        return 0

    current_revisions = asyncio.run(_get_alembic_revisions(settings.database_url))
    if not current_revisions:
        baseline_revision = os.getenv(_BASELINE_ENV_VAR)
        if not baseline_revision:
            raise SystemExit(
                "Database contains application tables but has no Alembic revision state. "
                f"Set {_BASELINE_ENV_VAR} to the matching revision or reset the staging RDS database "
                "before running migrations."
            )

        logger.warning("Stamping existing schema at Alembic revision %s.", baseline_revision)
        _stamp_revision(baseline_revision)
        current_revisions = [baseline_revision]

    logger.info("Current Alembic revisions before upgrade: %s", ", ".join(current_revisions))
    logger.warning("Running alembic upgrade head.")
    _upgrade_head()

    final_revisions = asyncio.run(_get_alembic_revisions(settings.database_url))
    logger.info("Alembic revisions after upgrade: %s", ", ".join(final_revisions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
