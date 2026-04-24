"""Bootstrap an empty database for clean environment cutovers."""

from __future__ import annotations

import asyncio
import logging

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.app.config import settings
from src.app.database import Base
from src.app.models import *  # noqa: F401,F403 - populate SQLAlchemy metadata
from src.app.models.user import User, UserRole, InviteStatus  # noqa: F401 - not re-exported from models package
from src.app.services.password_service import PasswordService

logger = logging.getLogger(__name__)

_ALEMBIC_VERSION_TABLE = "alembic_version"


async def _create_super_admin(database_url: str) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(database_url, echo=False, poolclass=NullPool)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with async_session() as session:
            email = "zulkhaifahmed@gmail.com"
            result = await session.execute(select(User).where(User.email == email))
            if not result.scalar_one_or_none():
                user = User(
                    email=email,
                    role=UserRole.SUPER_ADMIN.value,
                    password_hash=PasswordService.hash_password("Levi@144"),
                    is_active=True,
                    invite_status=InviteStatus.ACCEPTED.value,
                )
                session.add(user)
                await session.commit()
                logger.info(f"Automatically bootstrapped SUPER_ADMIN user: {email}")
    except Exception as e:
        logger.error(f"Failed to bootstrap SUPER_ADMIN: {e}")
    finally:
        await engine.dispose()


async def _list_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url, echo=False, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
            return set(table_names)
    finally:
        await engine.dispose()


async def _create_schema(database_url: str) -> None:
    engine = create_async_engine(database_url, echo=False, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    finally:
        await engine.dispose()


def _stamp_head() -> None:
    alembic_config = Config("alembic.ini")
    command.stamp(alembic_config, "head")


async def bootstrap_database_if_empty(database_url: str) -> bool:
    """Create the current schema when the database is empty."""
    table_names = await _list_tables(database_url)
    application_tables = table_names - {_ALEMBIC_VERSION_TABLE}
    if application_tables:
        logger.info("Database already has application tables; skipping bootstrap.")
        return False

    logger.warning("Database is empty; creating schema from SQLAlchemy metadata.")
    await _create_schema(database_url)
    return True


def ensure_database_bootstrapped(database_url: str) -> bool:
    """Create the current schema, then stamp Alembic head outside the async loop."""
    bootstrapped = asyncio.run(bootstrap_database_if_empty(database_url))
    if bootstrapped:
        logger.warning("Stamping Alembic head after bootstrap schema creation.")
        _stamp_head()
    
    return bootstrapped


def main() -> int:
    if not settings.database_url:
        logger.info("DATABASE_URL is not set; skipping bootstrap.")
        return 0

    ensure_database_bootstrapped(settings.database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
