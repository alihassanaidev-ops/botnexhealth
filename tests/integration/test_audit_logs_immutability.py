"""HIPAA §164.312(b): the audit log must be tamper-resistant at the DB.

The baseline migration installs row-level BEFORE UPDATE / BEFORE DELETE
triggers that raise an exception. The 20260516 migration adds the
matching statement-level BEFORE TRUNCATE trigger so the third write
path is also closed. Verify all three against a real Postgres,
running both as the privileged owner role (which would otherwise have
ALL on the table) and as the runtime ``nexhealth_app`` role (which
should be locked to SELECT/INSERT only).
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.rls

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pg_database_url() -> str:
    postgres_module = pytest.importorskip("testcontainers.postgres")
    PostgresContainer = postgres_module.PostgresContainer

    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover - depends on local Docker
        pytest.skip(f"Postgres Testcontainer unavailable: {exc}")

    try:
        raw_url = container.get_connection_url()
        yield _asyncpg_url(raw_url)
    finally:
        container.stop()


@pytest_asyncio.fixture(scope="module")
async def pg_engine(pg_database_url: str):
    """Run the baseline + truncate migrations and create the app role."""
    await _apply_migration(pg_database_url, "20260510_consolidated_baseline.py")
    await _apply_migration(
        pg_database_url, "20260516_audit_logs_truncate_protection.py"
    )
    await _create_app_role(pg_database_url)

    engine = create_async_engine(pg_database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


# =============================================================================
# Tests — admin role (would otherwise have full table privileges)
# =============================================================================

@pytest.mark.asyncio
async def test_admin_can_insert_but_not_update_audit_row(pg_engine) -> None:
    row_id = await _insert_seed_row(pg_engine)

    async with pg_engine.begin() as conn:
        # SELECT works.
        existing = await conn.scalar(
            text("SELECT outcome FROM audit_logs WHERE id = :id"),
            {"id": row_id},
        )
        assert existing == "SUCCESS"

        # UPDATE is blocked by trigger, even for the privileged owner.
        with pytest.raises(DBAPIError) as exc:
            await conn.execute(
                text("UPDATE audit_logs SET outcome = 'TAMPERED' WHERE id = :id"),
                {"id": row_id},
            )
        assert "append-only" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_admin_cannot_delete_audit_row(pg_engine) -> None:
    row_id = await _insert_seed_row(pg_engine)

    async with pg_engine.begin() as conn:
        with pytest.raises(DBAPIError) as exc:
            await conn.execute(
                text("DELETE FROM audit_logs WHERE id = :id"),
                {"id": row_id},
            )
        assert "append-only" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_admin_cannot_truncate_audit_logs(pg_engine) -> None:
    """The new BEFORE TRUNCATE trigger blocks parent-table truncate."""
    await _insert_seed_row(pg_engine)

    async with pg_engine.begin() as conn:
        with pytest.raises(DBAPIError) as exc:
            await conn.execute(text("TRUNCATE TABLE audit_logs"))
        assert "append-only" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_admin_cannot_truncate_audit_logs_cascade(pg_engine) -> None:
    """CASCADE form must also be blocked — same trigger, same exception."""
    await _insert_seed_row(pg_engine)
    async with pg_engine.begin() as conn:
        with pytest.raises(DBAPIError) as exc:
            await conn.execute(text("TRUNCATE TABLE audit_logs CASCADE"))
        assert "append-only" in str(exc.value).lower()


# =============================================================================
# Tests — runtime app role (the credential the application actually uses)
# =============================================================================

@pytest.mark.asyncio
async def test_app_role_cannot_update_or_delete_or_truncate(pg_database_url, pg_engine) -> None:
    """The runtime role is locked to SELECT/INSERT — verify all three writes
    are rejected, regardless of trigger state.

    Note: ``audit_logs`` has RLS enabled. Set ``app.context_type='audit'``
    so the seed row is visible to the app role; otherwise UPDATE/DELETE
    would silently affect 0 rows and the trigger would never fire.
    TRUNCATE is statement-level and bypasses RLS row-filtering.
    """
    # Seed a row as the privileged owner.
    row_id = await _insert_seed_row(pg_engine)

    app_url = _database_url_with_credentials(
        pg_database_url, username="nexhealth_app", password="nexhealth_app"
    )
    app_engine = create_async_engine(app_url, poolclass=NullPool)
    try:
        # The runtime grant is SELECT, INSERT only — UPDATE/DELETE/TRUNCATE
        # are rejected at the GRANT layer (insufficient_privilege) before
        # the trigger even fires. That's exactly the production posture, so
        # accept either failure mode (privilege OR trigger). Both close the
        # write path; defense-in-depth means we don't care which one trips.
        def _is_immutability_error(err: Exception) -> bool:
            msg = str(err).lower()
            return (
                "permission denied" in msg
                or "insufficient" in msg
                or "append-only" in msg
            )

        async with app_engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.context_type', 'audit', false)")
            )
            with pytest.raises(DBAPIError) as exc:
                await conn.execute(
                    text("UPDATE audit_logs SET outcome = 'TAMPERED' WHERE id = :id"),
                    {"id": row_id},
                )
            assert _is_immutability_error(exc.value), str(exc.value)

        async with app_engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.context_type', 'audit', false)")
            )
            with pytest.raises(DBAPIError) as exc:
                await conn.execute(
                    text("DELETE FROM audit_logs WHERE id = :id"),
                    {"id": row_id},
                )
            assert _is_immutability_error(exc.value), str(exc.value)

        async with app_engine.begin() as conn:
            with pytest.raises(DBAPIError) as exc:
                await conn.execute(text("TRUNCATE TABLE audit_logs"))
            assert _is_immutability_error(exc.value), str(exc.value)
    finally:
        await app_engine.dispose()


# =============================================================================
# Helpers
# =============================================================================

async def _insert_seed_row(engine) -> str:
    """Insert one valid audit row as the privileged owner. Return its id."""
    row_id = str(uuid.uuid4())
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO audit_logs (
                    id, "timestamp", actor, action, target_resource, outcome
                ) VALUES (
                    :id, :ts, 'ADMIN', 'LOGIN', 'user:test', 'SUCCESS'
                )
                """
            ),
            {"id": row_id, "ts": datetime.now(timezone.utc)},
        )
    return row_id


def _asyncpg_url(raw_url: str) -> str:
    if raw_url.startswith("postgresql+asyncpg://"):
        return raw_url
    if raw_url.startswith("postgresql+psycopg2://"):
        return raw_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if raw_url.startswith("postgresql://"):
        return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw_url


def _database_url_with_credentials(
    database_url: str, *, username: str, password: str
) -> str:
    return make_url(database_url).set(
        username=username, password=password
    ).render_as_string(hide_password=False)


async def _apply_migration(database_url: str, filename: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_run_migration, filename)
    finally:
        await engine.dispose()


def _run_migration(sync_conn, filename: str) -> None:  # noqa: ANN001
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    migration_path = ROOT / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(
        f"_migration_{filename.removesuffix('.py')}", migration_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load migration from {migration_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    context = MigrationContext.configure(sync_conn)
    operations = Operations(context)
    original_op = module.op
    module.op = operations
    try:
        module.upgrade()
    finally:
        module.op = original_op


async def _create_app_role(database_url: str) -> None:
    """Create the runtime ``nexhealth_app`` role and grant SELECT/INSERT only.

    Mirrors the production GRANT pattern (partition migration line 265).
    """
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') "
                    "THEN CREATE ROLE nexhealth_app LOGIN PASSWORD 'nexhealth_app'; END IF; END $$"
                )
            )
            await conn.execute(text("GRANT USAGE ON SCHEMA public TO nexhealth_app"))
            # Match the production grants — SELECT, INSERT only on audit_logs.
            await conn.execute(
                text("GRANT SELECT, INSERT ON audit_logs TO nexhealth_app")
            )
            await conn.execute(
                text("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO nexhealth_app")
            )
    finally:
        await engine.dispose()
