"""Async database connection for PostgreSQL."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import AsyncGenerator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


logger = logging.getLogger(__name__)


def _validate_uuid_or_empty(value: str | None) -> str:
    """Return validated UUID string or "" for None/empty/invalid input.

    Fails closed: invalid UUIDs become empty strings, which won't match any
    RLS policy, so access is denied rather than allowed via injection.
    """
    if not value:
        return ""
    try:
        UUID(str(value))
        return str(value)
    except (ValueError, TypeError, AttributeError):
        logger.warning(
            "Invalid UUID in RLS context: %r — treating as empty", value
        )
        return ""


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    pass


# Engine will be initialized on startup
_engine = None
_session_factory = None
_rls_context: ContextVar["RlsContext | None"] = ContextVar("rls_context", default=None)


@dataclass(frozen=True)
class RlsContext:
    """PostgreSQL RLS context applied to every DB transaction."""

    context_type: str
    user_id: str | None = None
    role: str | None = None
    institution_id: str | None = None
    location_id: str | None = None
    external_id: str | None = None
    # Set for GROUP_ADMIN sessions. Drives the group RLS read policies on
    # institutions / institution_groups; member data reads still set
    # institution_id per-request (see the /group aggregate loop).
    group_id: str | None = None

    @classmethod
    def for_user(cls, user) -> "RlsContext":  # noqa: ANN001
        return cls(
            context_type="user",
            user_id=str(user.id) if getattr(user, "id", None) else None,
            role=getattr(user, "role", None),
            institution_id=(
                str(user.institution_id)
                if getattr(user, "institution_id", None)
                else None
            ),
            location_id=(
                str(user.location_id) if getattr(user, "location_id", None) else None
            ),
            group_id=(
                str(user.group_id) if getattr(user, "group_id", None) else None
            ),
        )

    @classmethod
    def system(
        cls,
        context_type: str,
        *,
        institution_id: str | None = None,
        location_id: str | None = None,
        user_id: str | None = None,
        role: str | None = None,
        external_id: str | None = None,
        group_id: str | None = None,
    ) -> "RlsContext":
        return cls(
            context_type=context_type,
            user_id=user_id,
            role=role,
            institution_id=institution_id,
            location_id=location_id,
            external_id=external_id,
            group_id=group_id,
        )


def is_database_initialized() -> bool:
    """Return True when the SQLAlchemy session factory has been initialized."""
    return _session_factory is not None


def current_rls_context() -> RlsContext | None:
    """Return the RLS context active for this request/task."""
    return _rls_context.get()


def set_current_rls_context(context: RlsContext | None) -> Token:
    """Set the request/task RLS context and return a reset token."""
    return _rls_context.set(context)


def reset_rls_context(token: Token) -> None:
    """Reset the request/task RLS context using a token."""
    _rls_context.reset(token)


def clear_current_rls_context() -> None:
    """Clear any request/task RLS context."""
    _rls_context.set(None)


@contextmanager
def use_rls_context(context: RlsContext | None):
    """Temporarily apply an RLS context to nested DB sessions."""
    token = set_current_rls_context(context)
    try:
        yield
    finally:
        reset_rls_context(token)


async def apply_rls_context(session: AsyncSession, context: RlsContext | None) -> None:
    """Apply context as transaction-local PostgreSQL settings.

    ``set_config(..., false)`` uses session scope so the context survives
    explicit commits inside a route. We clear it in ``finally`` before the
    pooled connection is reused.

    All six settings are applied in a single round-trip via a batched
    ``SELECT set_config(...), set_config(...), ...`` to avoid N+1 latency
    on every request. UUID-shaped fields (``user_id``, ``institution_id``,
    ``location_id``) are validated before binding so a malformed value
    fails closed (empty string) rather than reaching the database.
    """
    await session.execute(
        text(
            "SELECT "
            "set_config('app.context_type', :context_type, false), "
            "set_config('app.user_id', :user_id, false), "
            "set_config('app.role', :role, false), "
            "set_config('app.institution_id', :institution_id, false), "
            "set_config('app.location_id', :location_id, false), "
            "set_config('app.external_id', :external_id, false), "
            "set_config('app.group_id', :group_id, false)"
        ),
        {
            "context_type": context.context_type if context else "",
            "user_id": _validate_uuid_or_empty(context.user_id) if context else "",
            "role": context.role if context and context.role else "",
            "institution_id": (
                _validate_uuid_or_empty(context.institution_id) if context else ""
            ),
            "location_id": (
                _validate_uuid_or_empty(context.location_id) if context else ""
            ),
            "external_id": (
                context.external_id if context and context.external_id else ""
            ),
            "group_id": (
                _validate_uuid_or_empty(context.group_id) if context else ""
            ),
        },
    )


async def clear_session_rls_context(session: AsyncSession) -> None:
    """Clear session-scoped RLS settings before returning a connection to the pool."""
    session.info["_skip_rls_reapply"] = True
    try:
        await apply_rls_context(session, None)
        await session.commit()
    finally:
        session.info.pop("_skip_rls_reapply", None)


class RlsAsyncSession(AsyncSession):
    """AsyncSession that preserves RLS context across explicit commits.

    SQLAlchemy may release a connection after ``commit()``/``rollback()`` and
    later check out a different pooled connection for ``refresh()`` or another
    query. PostgreSQL GUCs are connection-local, so reapply the active request
    context after transaction boundaries before route code continues.
    """

    async def commit(self) -> None:
        await super().commit()
        await self._reapply_rls_context_after_boundary()

    async def rollback(self) -> None:
        await super().rollback()
        await self._reapply_rls_context_after_boundary()

    async def _reapply_rls_context_after_boundary(self) -> None:
        if self.info.get("_skip_rls_reapply"):
            return
        context = current_rls_context()
        if context is not None:
            await apply_rls_context(self, context)


def init_database(database_url: str, *, use_null_pool: bool = False) -> None:
    """
    Initialize the database engine and session factory.

    Args:
        database_url: PostgreSQL connection string (asyncpg format).
        use_null_pool: When True, the engine uses :class:`NullPool` so that
            no asyncpg connection is ever cached in the pool. Required for
            Celery prefork workers, where each task runs inside its own
            ``asyncio.run()`` event loop — a pooled asyncpg connection would
            be bound to the loop of whichever task created it and raise
            ``RuntimeError: ... attached to a different loop`` on the next
            task. With NullPool, every checkout opens a fresh connection on
            the current loop and closes it on checkin.
    """
    global _engine, _session_factory
    from src.app.config import settings

    if use_null_pool:
        from sqlalchemy.pool import NullPool

        _engine = create_async_engine(
            database_url,
            echo=False,
            poolclass=NullPool,
        )
    else:
        _engine = create_async_engine(
            database_url,
            echo=False,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_recycle=settings.database_pool_recycle_seconds,
            pool_pre_ping=True,
        )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=RlsAsyncSession,
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
        await apply_rls_context(session, current_rls_context())
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await clear_session_rls_context(session)
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
        await apply_rls_context(session, current_rls_context())
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await clear_session_rls_context(session)
        await session.close()


@asynccontextmanager
async def get_user_db_session(user) -> AsyncGenerator[AsyncSession, None]:  # noqa: ANN001
    """Open a DB session under the authenticated user's RLS context."""
    with use_rls_context(RlsContext.for_user(user)):
        async with get_db_session() as session:
            yield session


@asynccontextmanager
async def get_system_db_session(
    context_type: str,
    *,
    institution_id: str | None = None,
    location_id: str | None = None,
    user_id: str | None = None,
    role: str | None = None,
    external_id: str | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Open a DB session under an explicit non-user RLS context."""
    with use_rls_context(
        RlsContext.system(
            context_type,
            institution_id=institution_id,
            location_id=location_id,
            user_id=user_id,
            role=role,
            external_id=external_id,
        )
    ):
        async with get_db_session() as session:
            yield session


async def create_tables() -> None:
    """Create all tables in the database if they don't exist."""
    if not _engine:
        raise RuntimeError("Database not initialized. Call init_database() first.")

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
