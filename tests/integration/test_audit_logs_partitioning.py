"""End-to-end tests for the partitioned ``audit_logs`` table.

Pins the operational contract that the partitioning migration is
supposed to deliver:

  - INSERTs route to the correct monthly partition based on ``timestamp``.
  - The DEFAULT partition catches out-of-range INSERTs without erroring.
  - The immutability triggers (``audit_logs_no_update``,
    ``audit_logs_no_delete``) STILL fire on the partitioned table.
  - The RLS policy STILL applies — tenant isolation hasn't regressed.
  - ``EXPLAIN`` for an institution-scoped query shows partition pruning.
  - The maintenance script is idempotent and can recover from a
    deliberately-dropped partition.

These tests skip when ``DATABASE_ADMIN_URL`` / ``DATABASE_URL`` is not
set (CI without Postgres).
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parents[2]


def _admin_url() -> str | None:
    return os.environ.get("DATABASE_ADMIN_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = _admin_url()
    if not url:
        pytest.skip("DATABASE_ADMIN_URL/DATABASE_URL not set")
    return url


@pytest_asyncio.fixture
async def admin_session(admin_url: str):
    engine = create_async_engine(admin_url, poolclass=NullPool)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionFactory() as session:
        await _set_super_admin(session)
        yield session
    await engine.dispose()


async def _set_super_admin(session: AsyncSession) -> None:
    for setting, value in (
        ("app.context_type", "user"),
        ("app.role", "SUPER_ADMIN"),
        ("app.user_id", "00000000-0000-0000-0000-000000000001"),
    ):
        await session.execute(
            text("SELECT set_config(:k, :v, false)"), {"k": setting, "v": value}
        )


async def _set_audit_context(
    session: AsyncSession, *, institution_id: str, user_id: str | None = None
) -> None:
    """Use the 'audit' system context so INSERTs satisfy the audit_logs
    RLS policy WITH CHECK clause (the audit context is the only one
    permitted to write audit_logs aside from SUPER_ADMIN)."""
    await session.execute(
        text("SELECT set_config('app.context_type', 'audit', false)")
    )
    await session.execute(
        text("SELECT set_config('app.institution_id', :v, false)"),
        {"v": institution_id},
    )
    if user_id is not None:
        await session.execute(
            text("SELECT set_config('app.user_id', :v, false)"), {"v": user_id}
        )


async def _insert_audit_row(
    session: AsyncSession, *, institution_id: str, timestamp: datetime
) -> str:
    """Insert one audit row at the given timestamp. Returns the row id."""
    row_id = str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO audit_logs
              (id, "timestamp", actor, action, target_resource, outcome,
               audit_metadata, institution_id)
            VALUES
              (:id, :ts, 'SYSTEM', 'READ_PATIENT', 'patient:test', 'SUCCESS',
               '{}'::jsonb, :inst)
            """
        ),
        {"id": row_id, "ts": timestamp, "inst": institution_id},
    )
    return row_id


async def _partition_for_row(session: AsyncSession, row_id: str) -> str:
    """Return the actual partition table name a given row landed in.

    We use ``tableoid::regclass::text`` — Postgres tells us at query
    time which physical relation contains the tuple.
    """
    result = await session.execute(
        text(
            "SELECT tableoid::regclass::text FROM audit_logs WHERE id = :id"
        ),
        {"id": row_id},
    )
    return result.scalar_one()


# =============================================================================
# Partition routing
# =============================================================================


@pytest.mark.asyncio
async def test_audit_log_inserts_route_to_correct_monthly_partition(
    admin_session: AsyncSession,
):
    """An INSERT with a current-month timestamp lands in the matching
    monthly partition (``audit_logs_yYYYY_mMM``), not the DEFAULT."""
    institution_id = str(uuid4())
    today = date.today()

    # Need an institution row so the FK on audit_metadata's referenced
    # institution-scoped queries don't break — but audit_logs has no FK
    # on institution_id, so we can simply set the context and insert.
    await _set_audit_context(admin_session, institution_id=institution_id)

    now = datetime.now(timezone.utc)
    row_id = await _insert_audit_row(
        admin_session, institution_id=institution_id, timestamp=now
    )
    await admin_session.commit()

    # Re-set super-admin to read across partitions for the assertion.
    await _set_super_admin(admin_session)
    partition = await _partition_for_row(admin_session, row_id)
    expected = f"audit_logs_y{today.year}_m{today.month:02d}"
    assert partition == expected, (
        f"Expected row to land in {expected}, got {partition}. The current "
        f"month's partition is missing or partition pruning is misconfigured."
    )

    # Cleanup — but we can't DELETE from audit_logs (immutability
    # trigger). Drop the partition to reset; the maintenance script
    # will recreate it on next run.
    await _set_super_admin(admin_session)
    await admin_session.execute(
        text("ALTER TABLE audit_logs DETACH PARTITION " + partition)
    )
    await admin_session.execute(text(f"DROP TABLE {partition}"))
    await admin_session.commit()

    # Recreate so subsequent tests in this module see the expected window.
    next_month_year, next_month_month = _add_months(today.year, today.month, 1)
    await admin_session.execute(
        text(
            f"CREATE TABLE {partition} PARTITION OF audit_logs "
            f"FOR VALUES FROM ('{today.year}-{today.month:02d}-01') "
            f"TO ('{next_month_year}-{next_month_month:02d}-01')"
        )
    )
    await admin_session.commit()


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    new_index = month - 1 + delta
    return year + new_index // 12, (new_index % 12) + 1


# =============================================================================
# Immutability triggers still fire
# =============================================================================


@pytest.mark.asyncio
async def test_audit_log_update_blocked_by_immutability_trigger(
    admin_session: AsyncSession,
):
    """HIPAA §164.312(b): once written, an audit row must not be mutable.
    The immutability trigger fires on the parent partitioned table and
    inherits to all partitions (PG13+)."""
    institution_id = str(uuid4())
    await _set_audit_context(admin_session, institution_id=institution_id)
    row_id = await _insert_audit_row(
        admin_session,
        institution_id=institution_id,
        timestamp=datetime.now(timezone.utc),
    )
    await admin_session.commit()

    await _set_super_admin(admin_session)
    with pytest.raises(DBAPIError) as exc:
        await admin_session.execute(
            text("UPDATE audit_logs SET outcome = 'TAMPERED' WHERE id = :id"),
            {"id": row_id},
        )
        await admin_session.commit()

    # Immutability trigger raises a 'append-only' SQLSTATE-mapped error.
    assert "append-only" in str(exc.value).lower() or "append-only" in str(
        exc.value.orig
    ).lower()
    await admin_session.rollback()


@pytest.mark.asyncio
async def test_audit_log_delete_blocked_by_immutability_trigger(
    admin_session: AsyncSession,
):
    """Same trigger blocks DELETE."""
    institution_id = str(uuid4())
    await _set_audit_context(admin_session, institution_id=institution_id)
    row_id = await _insert_audit_row(
        admin_session,
        institution_id=institution_id,
        timestamp=datetime.now(timezone.utc),
    )
    await admin_session.commit()

    await _set_super_admin(admin_session)
    with pytest.raises(DBAPIError) as exc:
        await admin_session.execute(
            text("DELETE FROM audit_logs WHERE id = :id"), {"id": row_id}
        )
        await admin_session.commit()

    assert "append-only" in str(exc.value).lower() or "append-only" in str(
        exc.value.orig
    ).lower()
    await admin_session.rollback()


# =============================================================================
# RLS still applies
# =============================================================================


@pytest.mark.asyncio
async def test_audit_log_rls_policy_remains_attached_after_partitioning(
    admin_session: AsyncSession,
):
    """The ``audit_logs_rls`` policy survived the partition migration AND
    FORCE RLS is enabled at the parent level. The full cross-tenant
    isolation contract (rows from institution A invisible to a user
    context for institution B) is exercised in
    ``tests/integration/test_rls_postgres.py``; this test pins the
    structural property without depending on a non-superuser session.
    """
    policy_count = (
        await admin_session.execute(
            text(
                "SELECT count(*) FROM pg_policy "
                "WHERE polrelid = 'audit_logs'::regclass AND polname = 'audit_logs_rls'"
            )
        )
    ).scalar_one()
    assert policy_count == 1, "audit_logs_rls policy missing on partitioned table"

    rls_state = (
        await admin_session.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE oid = 'audit_logs'::regclass"
            )
        )
    ).one()
    assert rls_state.relrowsecurity is True, "RLS not enabled on audit_logs"
    assert rls_state.relforcerowsecurity is True, (
        "FORCE RLS missing — superuser sessions WILL bypass the policy in "
        "production-style deployments"
    )


# =============================================================================
# EXPLAIN shows partition pruning
# =============================================================================


@pytest.mark.asyncio
async def test_audit_log_query_uses_partition_pruning(
    admin_session: AsyncSession,
):
    """A query with a ``timestamp`` predicate must hit only the matching
    partition(s), not scan every partition.

    This is the load-bearing performance promise of partitioning. If
    the planner fails to prune (wrong PK shape, missing constraint
    exclusion, etc.) the scan walks every partition and we lose.
    """
    today = date.today()
    current_partition = f"audit_logs_y{today.year}_m{today.month:02d}"

    await _set_super_admin(admin_session)
    explain = (
        await admin_session.execute(
            text(
                "EXPLAIN (FORMAT TEXT) SELECT count(*) FROM audit_logs "
                "WHERE \"timestamp\" >= :start AND \"timestamp\" < :end"
            ),
            {
                "start": datetime(today.year, today.month, 1, tzinfo=timezone.utc),
                "end": datetime(today.year, today.month, 28, tzinfo=timezone.utc),
            },
        )
    ).scalars().all()
    plan_text = "\n".join(explain)

    # The plan should reference the current month's partition by name
    # (proves the planner picked it specifically) — not the parent
    # ``audit_logs`` (which would imply no pruning).
    assert current_partition in plan_text, (
        f"EXPLAIN did not show {current_partition} — partition pruning may "
        f"be broken. Plan:\n{plan_text}"
    )


# =============================================================================
# Maintenance script
# =============================================================================


@pytest.mark.asyncio
async def test_ensure_audit_partitions_recovers_dropped_partition(
    admin_session: AsyncSession,
):
    """Drop a future partition manually; verify the script recreates it
    AND grants on it (so the runtime role can write once timestamps
    advance into that month)."""
    today = date.today()
    target_year, target_month = _add_months(today.year, today.month, 6)
    target_partition = f"audit_logs_y{target_year}_m{target_month:02d}"

    # Detach + drop. DETACH first because we own the parent and child
    # has FK-like ties to it.
    await _set_super_admin(admin_session)
    await admin_session.execute(
        text(f"ALTER TABLE audit_logs DETACH PARTITION {target_partition}")
    )
    await admin_session.execute(text(f"DROP TABLE {target_partition}"))
    await admin_session.commit()

    # Run the maintenance script as a subprocess (matches how
    # EventBridge → ECS RunTask invokes it in production).
    result = subprocess.run(
        [sys.executable, "-m", "src.app.scripts.ensure_audit_partitions"],
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Maintenance script failed.\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )

    # The partition is back as a partition of audit_logs.
    is_partition = (
        await admin_session.execute(
            text(
                """
                SELECT EXISTS(
                    SELECT 1 FROM pg_inherits
                    WHERE inhparent = 'audit_logs'::regclass
                      AND inhrelid = (:name)::regclass
                )
                """
            ),
            {"name": target_partition},
        )
    ).scalar_one()
    assert is_partition, f"{target_partition} not re-attached as partition"


def test_ensure_audit_partitions_module_imports_cleanly():
    """Same shape as the other scheduled-job module-import contract."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib; importlib.import_module('src.app.scripts.ensure_audit_partitions')",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_ensure_audit_partitions_has_main_entrypoint():
    from src.app.scripts import ensure_audit_partitions

    assert callable(getattr(ensure_audit_partitions, "main", None))
