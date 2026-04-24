"""Destructively reset the configured database schema."""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.app.config import settings

logger = logging.getLogger(__name__)

_CONFIRM_ENV_VAR = "ALLOW_DESTRUCTIVE_RESET"


async def _reset_public_schema(database_url: str) -> None:
    engine = create_async_engine(database_url, echo=False, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND pid <> pg_backend_pid()
                    """
                )
            )
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        logger.warning("Reset public schema for database.")
    finally:
        await engine.dispose()


def main() -> int:
    if os.getenv(_CONFIRM_ENV_VAR) != "1":
        raise SystemExit(
            f"Refusing to reset the database. Set {_CONFIRM_ENV_VAR}=1 to confirm the destructive action."
        )

    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set.")

    logger.warning("Destructively resetting the configured database.")
    asyncio.run(_reset_public_schema(settings.database_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
