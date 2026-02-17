"""Async database connection for PostgreSQL (Supabase)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    pass


# Engine will be initialized on startup
_engine = None
_session_factory = None


def init_database(database_url: str) -> None:
    """
    Initialize the database engine and session factory.
    
    Args:
        database_url: PostgreSQL connection string (asyncpg format)
    """
    global _engine, _session_factory
    
    from sqlalchemy.pool import NullPool
    
    _engine = create_async_engine(
        database_url,
        echo=False,  # Set to True for SQL debugging
        poolclass=NullPool,  # Use NullPool for Supabase Transaction Mode (port 6543)
        # No pool_size/max_overflow needed with NullPool - connections are created/closed per request
    )
    
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def close_database() -> None:
    """Close database connections."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get an async database session.
    
    Usage:
        async with get_db_session() as session:
            result = await session.execute(...)
    """
    if not _session_factory:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    
    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db_session_dep() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI-compatible dependency for database sessions.

    Use this with ``Depends(get_db_session_dep)`` in route signatures.
    The plain ``get_db_session`` (decorated with @asynccontextmanager)
    should only be used with ``async with get_db_session() as session:``.
    """
    if not _session_factory:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def create_tables() -> None:
    """Create all tables in the database if they don't exist."""
    if not _engine:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
