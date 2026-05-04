"""
Alembic environment configuration for async SQLAlchemy.

Reads DATABASE_URL from the application Settings (which reads .env / env vars).
Imports all models so Alembic's autogenerate can detect schema changes.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

# -- App imports ---------------------------------------------------------------
# Import Settings to get DATABASE_URL from .env / env vars
from src.app.config import settings

# Import Base and ALL models so metadata is populated for autogenerate
from src.app.database import Base
from src.app.models import (  # noqa: F401 — side-effect import
    AuditLog,
    Call,
    Contact,
    ContactLocationAccess,
    CustomFieldDefinition,
    CustomFieldValue,
    InsurancePlan,
    LocationBreak,
    LocationOperatingHours,
    Notification,
    RetellWebhookEvent,
    Institution,
    InstitutionAppointmentType,
    InstitutionDescriptor,
    InstitutionLocation,
    InstitutionOperatory,
    InstitutionProvider,
    SmsHistoryLog,
)
from src.app.models.user import User  # noqa: F401

# -- Alembic config -----------------------------------------------------------
config = context.config

# Configure logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object for autogenerate support
target_metadata = Base.metadata


def get_database_url() -> str:
    """Get the async database URL from application settings."""
    url = settings.database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Alembic needs it to run migrations. "
            "Set it in .env or as an environment variable."
        )
    return url


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — generates SQL without connecting.

    Useful for generating SQL scripts to review or run manually.
    Usage: alembic upgrade head --sql
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Run migrations with the given connection."""
    # Set SUPER_ADMIN RLS context so migrations can see/touch all rows under
    # FORCE ROW LEVEL SECURITY. The zero-UUID is the recognized system bootstrap
    # identity. Without this, raw-engine migrations evaluate every policy to
    # false and silently affect 0 rows.
    connection.execute(
        text(
            "SELECT set_config('app.context_type', 'user', false), "
            "set_config('app.role', 'SUPER_ADMIN', false), "
            "set_config('app.user_id', :uid, false)"
        ),
        {"uid": "00000000-0000-0000-0000-000000000000"},
    )

    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode — connects to the real database."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online (connected) migrations."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
