"""End-to-end integration tests for scheduled background jobs.

Each scheduled job (recompute_dashboard_rollup, cleanup_idempotency) is
run as a *subprocess* — the same way EventBridge → ECS RunTask invokes
it in production (``python -m src.app.scripts.<script>``). The subprocess
inherits the local environment (DATABASE_URL / DATABASE_ADMIN_URL), seeds
data via a fixture, and we assert post-conditions.

Why subprocess instead of importing the function: production runs the
script with a fresh interpreter, fresh logging config, fresh argv
parsing. An import-based test misses module-level side effects (logger
config, asyncio.run() vs an existing loop, argv parsing). The
subprocess invocation matches the deploy artifact byte-for-byte.

These tests skip when ``DATABASE_ADMIN_URL`` is not set (CI-without-
postgres path) and write under the dedicated ``-scheduled-jobs-``
institution slug so they can't collide with other tests' data.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parents[2]

# Stable identifiers for fixture data; chosen so they're easy to spot in
# Postgres if a test crashes mid-run and leaves rows behind.
TEST_INSTITUTION_ID = "abcabcab-abca-4bca-8bca-aaaaaaaaaaaa"
TEST_LOCATION_ID = "abcabcab-abca-4bca-8bca-bbbbbbbbbbbb"
TEST_INSTITUTION_SLUG = "scheduled-jobs-test"

pytestmark = pytest.mark.integration


def _admin_url() -> str | None:
    """Resolve the DSN every script will use as the admin role."""
    return os.environ.get("DATABASE_ADMIN_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def admin_url() -> str:
    url = _admin_url()
    if not url:
        pytest.skip("DATABASE_ADMIN_URL/DATABASE_URL not set — skipping live integration")
    return url


@pytest_asyncio.fixture
async def admin_session(admin_url: str):
    """Yield an admin SQLAlchemy session that bypasses RLS for setup/teardown.

    We set the SUPER_ADMIN context inside the session so the seed/cleanup
    queries can write to every protected table. Each test gets a fresh
    session so concurrent tests don't share transaction state.
    """
    engine = create_async_engine(admin_url, poolclass=NullPool)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)

    async with SessionFactory() as session:
        await _set_super_admin_rls(session)
        yield session

    await engine.dispose()


async def _set_super_admin_rls(session: AsyncSession) -> None:
    """Pin the session to a SUPER_ADMIN context so RLS lets us write
    fixture rows across tenants."""
    for setting, value in (
        ("app.context_type", "user"),
        ("app.role", "SUPER_ADMIN"),
        ("app.user_id", "00000000-0000-0000-0000-000000000001"),
    ):
        await session.execute(
            text("SELECT set_config(:k, :v, false)"), {"k": setting, "v": value}
        )


async def _purge_test_data(session: AsyncSession) -> None:
    """Drop everything this module's tests created so reruns are clean."""
    await session.execute(
        text("DELETE FROM call_metrics_daily WHERE institution_id = :i"),
        {"i": TEST_INSTITUTION_ID},
    )
    await session.execute(
        text("DELETE FROM calls WHERE institution_id = :i"),
        {"i": TEST_INSTITUTION_ID},
    )
    await session.execute(
        text(
            "DELETE FROM retell_function_invocations WHERE call_id LIKE 'sched-test-%'"
        )
    )
    await session.execute(
        text("DELETE FROM retell_webhook_events WHERE call_id LIKE 'sched-test-%'")
    )
    await session.execute(
        text("DELETE FROM institution_locations WHERE institution_id = :i"),
        {"i": TEST_INSTITUTION_ID},
    )
    await session.execute(
        text("DELETE FROM institutions WHERE id = :i"),
        {"i": TEST_INSTITUTION_ID},
    )
    await session.commit()


def _run_script(module_path: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Invoke a scheduled-job script as a subprocess.

    Mirrors the production invocation: same interpreter, same module
    spec, same env-var driven configuration. We capture stdout/stderr
    so failures are easy to debug from pytest output.
    """
    base_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", module_path],
        cwd=str(REPO_ROOT),
        env=base_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# =============================================================================
# recompute_dashboard_rollup
# =============================================================================


@pytest.mark.asyncio
async def test_recompute_dashboard_rollup_populates_rows_from_calls(
    admin_session: AsyncSession,
):
    """End-to-end: seed calls, run the script, assert rollup rows match.

    This is the contract every dashboard load depends on — if the
    recompute is broken, every clinic's volume cards eventually drift
    from reality. Sized to mirror a realistic mid-day window.
    """
    await _purge_test_data(admin_session)

    today = date.today()
    yesterday = today - timedelta(days=1)

    # Seed an institution + location, then 5 calls today and 3 yesterday
    # across multiple statuses (so tag_counts is also exercised).
    await admin_session.execute(
        text(
            "INSERT INTO institutions (id, name, slug, is_active) "
            "VALUES (:id, :name, :slug, true)"
        ),
        {
            "id": TEST_INSTITUTION_ID,
            "name": "Scheduled Jobs Test Clinic",
            "slug": TEST_INSTITUTION_SLUG,
        },
    )
    await admin_session.execute(
        text(
            "INSERT INTO institution_locations "
            "(id, institution_id, name, slug, is_active, timezone) "
            "VALUES (:id, :inst, 'Main', 'main', true, 'UTC')"
        ),
        {"id": TEST_LOCATION_ID, "inst": TEST_INSTITUTION_ID},
    )

    seeded = [
        (today,     "appointment_booked",  True,  False, 100),
        (today,     "needs_callback",      False, False, 80),
        (today,     "appointment_booked",  False, False, 60),
        (today,     "no_action_needed",    False, False, 40),
        (today,     "appointment_booked",  True,  False, 120),
        (yesterday, "appointment_booked",  False, False, 200),
        (yesterday, "complaint",           False, True,  150),
        (yesterday, "needs_callback",      False, False, 100),
    ]
    for index, (call_date, status, is_new, is_complaint, duration) in enumerate(seeded):
        await admin_session.execute(
            text(
                """
                INSERT INTO calls
                  (id, institution_id, location_id, retell_call_id, call_status,
                   call_date, call_duration_seconds, is_new_patient, is_complaint,
                   is_insurance_billing, callback_resolved, times_called,
                   created_at, updated_at)
                VALUES
                  (:id, :inst, :loc, :rcid, :status, :date, :dur, :inp, :ic,
                   false, false, 1, now(), now())
                """
            ),
            {
                "id": str(uuid4()),
                "inst": TEST_INSTITUTION_ID,
                "loc": TEST_LOCATION_ID,
                "rcid": f"sched-test-rollup-{index}",
                "status": status,
                "date": call_date,
                "dur": duration,
                "inp": is_new,
                "ic": is_complaint,
            },
        )
    await admin_session.commit()

    result = _run_script("src.app.scripts.recompute_dashboard_rollup")

    assert result.returncode == 0, (
        f"Script exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "recompute complete" in result.stdout.lower() or "recompute complete" in result.stderr.lower(), (
        f"Expected completion log line. stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    # Post-condition: two rows in call_metrics_daily for our institution
    # (today and yesterday) with totals matching the seed counts.
    rollup_rows = (
        await admin_session.execute(
            text(
                """
                SELECT call_date, total_calls, new_patient_calls, complaint_calls,
                       total_duration_seconds, tag_counts
                FROM call_metrics_daily
                WHERE institution_id = :i
                ORDER BY call_date DESC
                """
            ),
            {"i": TEST_INSTITUTION_ID},
        )
    ).all()
    assert len(rollup_rows) == 2, f"Expected 2 rollup rows, got {len(rollup_rows)}"

    today_row = rollup_rows[0]
    assert today_row.call_date == today
    assert today_row.total_calls == 5
    assert today_row.new_patient_calls == 2
    assert today_row.complaint_calls == 0
    assert today_row.total_duration_seconds == 400  # 100+80+60+40+120
    assert today_row.tag_counts.get("appointment_booked") == 3
    assert today_row.tag_counts.get("needs_callback") == 1
    assert today_row.tag_counts.get("no_action_needed") == 1

    yesterday_row = rollup_rows[1]
    assert yesterday_row.call_date == yesterday
    assert yesterday_row.total_calls == 3
    assert yesterday_row.complaint_calls == 1
    assert yesterday_row.total_duration_seconds == 450

    await _purge_test_data(admin_session)


@pytest.mark.asyncio
async def test_recompute_dashboard_rollup_is_idempotent(
    admin_session: AsyncSession,
):
    """Running the script twice in a row must produce identical rollup.

    Captures the contract that a stuck schedule (firing twice in quick
    succession during a deploy, or a manual rerun for ops) doesn't
    double-count or otherwise corrupt the rollup.
    """
    await _purge_test_data(admin_session)
    today = date.today()
    await admin_session.execute(
        text(
            "INSERT INTO institutions (id, name, slug, is_active) "
            "VALUES (:id, :name, :slug, true)"
        ),
        {
            "id": TEST_INSTITUTION_ID,
            "name": "Idempotency Test",
            "slug": TEST_INSTITUTION_SLUG,
        },
    )
    await admin_session.execute(
        text(
            "INSERT INTO institution_locations "
            "(id, institution_id, name, slug, is_active, timezone) "
            "VALUES (:id, :inst, 'Main', 'main', true, 'UTC')"
        ),
        {"id": TEST_LOCATION_ID, "inst": TEST_INSTITUTION_ID},
    )
    await admin_session.execute(
        text(
            """
            INSERT INTO calls
              (id, institution_id, location_id, retell_call_id, call_status,
               call_date, call_duration_seconds, is_new_patient, is_complaint,
               is_insurance_billing, callback_resolved, times_called,
               created_at, updated_at)
            VALUES
              (:id, :inst, :loc, :rcid, 'appointment_booked', :date, 60,
               false, false, false, false, 1, now(), now())
            """
        ),
        {
            "id": str(uuid4()),
            "inst": TEST_INSTITUTION_ID,
            "loc": TEST_LOCATION_ID,
            "rcid": "sched-test-idem-1",
            "date": today,
        },
    )
    await admin_session.commit()

    first = _run_script("src.app.scripts.recompute_dashboard_rollup")
    second = _run_script("src.app.scripts.recompute_dashboard_rollup")
    assert first.returncode == 0 and second.returncode == 0

    rows = (
        await admin_session.execute(
            text(
                "SELECT total_calls, total_duration_seconds, tag_counts "
                "FROM call_metrics_daily "
                "WHERE institution_id = :i AND call_date = :d"
            ),
            {"i": TEST_INSTITUTION_ID, "d": today},
        )
    ).all()
    assert len(rows) == 1, f"Idempotency violated: got {len(rows)} rows"
    assert rows[0].total_calls == 1
    assert rows[0].total_duration_seconds == 60

    await _purge_test_data(admin_session)


@pytest.mark.asyncio
async def test_recompute_dashboard_rollup_returns_nonzero_on_db_error(tmp_path):
    """Connection failure → exit code 1.

    EventBridge alarms on TaskFailed; the script MUST exit non-zero on
    any unhandled exception so the alarm fires. Using a deliberately
    invalid DSN that resolves quickly so the test stays fast.
    """
    env = {
        "DATABASE_URL": "postgresql+asyncpg://nope:nope@127.0.0.1:1/nope",
        "DATABASE_ADMIN_URL": "postgresql+asyncpg://nope:nope@127.0.0.1:1/nope",
    }
    result = _run_script("src.app.scripts.recompute_dashboard_rollup", env=env)
    assert result.returncode != 0, (
        "Script returned 0 with an unreachable DB — alarm path is broken"
    )


# =============================================================================
# cleanup_idempotency
# =============================================================================


@pytest.mark.asyncio
async def test_cleanup_idempotency_prunes_old_rows_keeps_recent(
    admin_session: AsyncSession,
):
    """Seed one ancient + one recent row in retell_function_invocations,
    run the cleanup, assert only the ancient row was deleted.

    This is the operational contract that bounds idempotency-table
    growth — without it, the table grows unbounded forever and every
    INSERT slows down with the ever-growing index.
    """
    # Three isolated rows so this test doesn't touch other test data.
    ancient_call = "sched-test-cleanup-ancient"
    recent_call = "sched-test-cleanup-recent"
    await admin_session.execute(
        text("DELETE FROM retell_function_invocations WHERE call_id LIKE 'sched-test-cleanup-%'")
    )

    ancient_ts = datetime.now(timezone.utc) - timedelta(days=60)
    for call_id, created_at in (
        (ancient_call, ancient_ts),
        (recent_call, datetime.now(timezone.utc)),
    ):
        await admin_session.execute(
            text(
                """
                INSERT INTO retell_function_invocations
                  (id, call_id, function_name, args_hash, status, attempts,
                   created_at, updated_at)
                VALUES
                  (gen_random_uuid(), :call_id, 'check_availability', :hash,
                   'COMPLETED', 1, :created_at, :created_at)
                """
            ),
            {
                "call_id": call_id,
                "hash": call_id,  # cheap unique hash
                "created_at": created_at,
            },
        )
    await admin_session.commit()

    result = _run_script("src.app.scripts.cleanup_idempotency")
    assert result.returncode == 0, (
        f"Cleanup exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    surviving = (
        await admin_session.execute(
            text(
                "SELECT call_id FROM retell_function_invocations "
                "WHERE call_id LIKE 'sched-test-cleanup-%' "
                "ORDER BY call_id"
            )
        )
    ).scalars().all()
    assert surviving == [recent_call], (
        f"Expected only the recent row to survive, got {surviving}"
    )

    # Tidy up the recent row.
    await admin_session.execute(
        text("DELETE FROM retell_function_invocations WHERE call_id = :c"),
        {"c": recent_call},
    )
    await admin_session.commit()


# =============================================================================
# Surface-level contract
# =============================================================================


@pytest.mark.parametrize(
    "module",
    [
        "src.app.scripts.recompute_dashboard_rollup",
        "src.app.scripts.cleanup_idempotency",
        "src.app.scripts.apply_retention_policy",
    ],
)
def test_scheduled_job_module_imports_cleanly(module: str):
    """Each scheduled-job module must import without side effects so a
    fresh container start can call ``python -m <module>`` safely.

    A bad import would surface as the EventBridge task failing
    immediately on every tick, which is recoverable but noisy. The
    test is fast and runs without a database.
    """
    result = subprocess.run(
        [sys.executable, "-c", f"import importlib; importlib.import_module('{module}')"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Importing {module} failed.\nstderr: {result.stderr}"
    )


def test_recompute_script_module_has_main_entrypoint():
    """``python -m`` requires ``main()`` semantics — guard the contract."""
    from src.app.scripts import recompute_dashboard_rollup

    assert callable(getattr(recompute_dashboard_rollup, "main", None)), (
        "recompute_dashboard_rollup.main() is missing — EventBridge "
        "would invoke `python -m` and find no entrypoint"
    )


def test_cleanup_script_module_has_main_entrypoint():
    from src.app.scripts import cleanup_idempotency

    assert callable(getattr(cleanup_idempotency, "main", None)), (
        "cleanup_idempotency.main() is missing"
    )


def test_apply_retention_policy_script_module_has_main_entrypoint():
    from src.app.scripts import apply_retention_policy

    assert callable(getattr(apply_retention_policy, "main", None)), (
        "apply_retention_policy.main() is missing"
    )
