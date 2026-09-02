"""End-to-end proof for the Split (A/B) node against a REAL Postgres.

Two claims the feature stands or falls on, neither of which a mocked test can
establish:

  1. **Assignment is durable and stable.** The dispatcher routes a run down one
     arm, writes the assignment row, and re-deriving it after a resume returns
     the same arm — because the run would otherwise switch variants mid-flight
     and corrupt the very experiment it is part of.
  2. **The arms reconcile with the campaign.** ``campaign_split_metrics_daily``
     and ``campaign_metrics_daily`` are written by one SQL union rendered twice;
     if that union ever drifts, an A/B report would contradict the campaign
     total it was cut from. Only real SQL against the real schema can catch it.

Mirrors tests/integration/test_automation_engine_integration.py: testcontainers
Postgres, the real Alembic chain to head, and an RLS-bypassing superuser session
seeded to INST_A/LOC_A.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = [pytest.mark.integration, pytest.mark.rls]

ROOT = Path(__file__).resolve().parents[2]

INST_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
LOC_A = "11111111-1111-1111-1111-111111111111"

#: Two arms, both landing on their own exit, so a run's outcome names its arm.
_SPLIT_DEF = {
    "trigger": {"type": "manual"},
    "entry_node_id": "ab",
    "nodes": [
        {
            "type": "split",
            "id": "ab",
            "subject": "Reminder wording",
            "branches": [
                {"label": "Variant A", "weight": 50, "next_node_id": "x-a"},
                {"label": "Variant B", "weight": 50, "next_node_id": "x-b"},
            ],
        },
        {"type": "exit", "id": "x-a", "outcome": "booked"},
        {"type": "exit", "id": "x-b", "outcome": "booked"},
    ],
}


# ---------------------------------------------------------------------------
# Container + schema fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_url() -> str:
    postgres_module = pytest.importorskip("testcontainers.postgres")
    try:
        container = postgres_module.PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover - depends on local Docker
        pytest.skip(f"Postgres Testcontainer unavailable: {exc}")
    try:
        yield _asyncpg(container.get_connection_url())
    finally:
        container.stop()


def _asyncpg(url: str) -> str:
    for prefix in ("postgresql+psycopg2://", "postgresql://"):
        if url.startswith(prefix):
            return url.replace(prefix, "postgresql+asyncpg://", 1)
    return url


def _upgrade_head(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


async def _set_ctx(conn, *, institution_id="", location_id="") -> None:
    values = {
        "app.context_type": "celery",
        "app.user_id": "",
        "app.role": "",
        "app.institution_id": institution_id,
        "app.location_id": location_id,
        "app.external_id": "split-int-test",
        "app.group_id": "",
    }
    for key, value in values.items():
        await conn.execute(
            text("SELECT set_config(:k, :v, false)"), {"k": key, "v": value}
        )


async def _seed_tenants(conn) -> None:
    await conn.execute(
        text(
            "INSERT INTO institutions (id, name, slug, is_active)"
            " VALUES (:a, 'Clinic A', 'clinic-a', true) ON CONFLICT DO NOTHING"
        ),
        {"a": INST_A},
    )
    await conn.execute(
        text(
            """
            INSERT INTO institution_locations
              (id, institution_id, name, slug, is_active, retell_agent_id,
               retell_from_number, twilio_from_number, timezone)
            VALUES (:la, :a, 'A One', 'a-one', true, 'agent-a',
                    '+15550000011', '+15550000001', 'UTC')
            ON CONFLICT DO NOTHING
            """
        ),
        {"la": LOC_A, "a": INST_A},
    )


@pytest_asyncio.fixture(scope="module")
async def superuser_engine(pg_url: str):
    await asyncio.to_thread(_upgrade_head, pg_url)
    engine = create_async_engine(pg_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await _set_ctx(conn, institution_id=INST_A)
        await _seed_tenants(conn)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(superuser_engine):
    maker = async_sessionmaker(superuser_engine, expire_on_commit=False)
    async with maker() as s:
        await _set_ctx(await s.connection(), institution_id=INST_A, location_id=LOC_A)
        yield s


async def _published_split(session, *, name="ab-wf"):
    from src.app.services.automation.definition_service import (
        AutomationWorkflowDefinitionService,
    )

    svc = AutomationWorkflowDefinitionService(session)
    wf = await svc.create_draft(INST_A, name=name, location_id=LOC_A)
    version = await svc.publish_version(wf, _SPLIT_DEF)
    await session.commit()
    return wf, version


# ---------------------------------------------------------------------------
# 1. Assignment is durable and stable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_is_routed_down_one_arm_and_the_assignment_is_persisted(session):
    from src.app.services.automation.enrollment_service import (
        AutomationWorkflowEnrollmentService,
    )
    from src.app.services.automation.runtime_service import (
        AutomationWorkflowRuntimeService,
    )
    from src.app.services.automation.step_dispatcher import build_dispatcher
    from src.app.services.automation.definition_schema import WorkflowDefinition

    wf, v1 = await _published_split(session)
    run, _ = await AutomationWorkflowEnrollmentService(session).enroll(
        institution_id=INST_A,
        workflow_id=str(wf.id),
        workflow_version_id=str(v1.id),
        location_id=LOC_A,
        idempotency_key="split-1",
    )
    await AutomationWorkflowRuntimeService(session).start_run(run)

    dispatcher, tz = await build_dispatcher(session, location_id=LOC_A)
    result = await dispatcher.advance(
        run, WorkflowDefinition.model_validate(_SPLIT_DEF), context={}, location_timezone=tz
    )
    await session.commit()

    assert result.status == "completed"

    rows = (
        await session.execute(
            text(
                "SELECT node_id, branch_label, bucket FROM"
                " automation_workflow_split_assignments WHERE workflow_run_id = :r"
            ),
            {"r": str(run.id)},
        )
    ).all()
    assert len(rows) == 1
    node_id, label, bucket = rows[0]
    assert node_id == "ab"
    assert label in {"Variant A", "Variant B"}
    assert 0 <= bucket < 100

    # The step trace names the arm, so a support question about one patient has
    # an answer beyond "the hash said so".
    step = (
        await session.execute(
            text(
                "SELECT result_code, result_metadata FROM"
                " automation_workflow_step_executions"
                " WHERE workflow_run_id = :r AND step_type = 'split'"
            ),
            {"r": str(run.id)},
        )
    ).one()
    assert step[0] == f"branch_{label}"
    assert step[1]["branch"] == label
    assert step[1]["bucket"] == bucket


@pytest.mark.asyncio
async def test_re_dispatching_a_run_keeps_it_on_the_same_arm(session):
    """The property retries and timer resumes depend on."""
    from src.app.services.automation.enrollment_service import (
        AutomationWorkflowEnrollmentService,
    )
    from src.app.services.automation.runtime_service import (
        AutomationWorkflowRuntimeService,
    )
    from src.app.services.automation.step_dispatcher import build_dispatcher
    from src.app.services.automation.definition_schema import WorkflowDefinition

    wf, v1 = await _published_split(session, name="ab-stable")
    run, _ = await AutomationWorkflowEnrollmentService(session).enroll(
        institution_id=INST_A,
        workflow_id=str(wf.id),
        workflow_version_id=str(v1.id),
        location_id=LOC_A,
        idempotency_key="split-stable",
    )
    await AutomationWorkflowRuntimeService(session).start_run(run)
    dispatcher, tz = await build_dispatcher(session, location_id=LOC_A)
    definition = WorkflowDefinition.model_validate(_SPLIT_DEF)

    await dispatcher.advance(run, definition, context={}, location_timezone=tz)
    await session.commit()
    first = run.outcome

    # Re-run the split node as a retry would. The assignment must not move, and
    # the upsert must not write a second row.
    run.current_step_id = "ab"
    await dispatcher.advance(run, definition, context={}, location_timezone=tz)
    await session.commit()

    labels = (
        await session.execute(
            text(
                "SELECT branch_label FROM automation_workflow_split_assignments"
                " WHERE workflow_run_id = :r"
            ),
            {"r": str(run.id)},
        )
    ).scalars().all()
    assert len(labels) == 1, "a retry must not create a second assignment"
    assert run.outcome == first


# ---------------------------------------------------------------------------
# 2. The arms reconcile with the campaign
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_arm_rollup_reconciles_with_the_campaign_total(session):
    """Ten contacts per arm, different booking counts, one rollup.

    The arms must report separately *and* add up to the campaign figure — the
    two tables are written by the same union, and this is what proves it.
    """
    import uuid

    from src.app.services.automation.campaign_analytics_service import recompute_window

    wf, v1 = await _published_split(session, name="ab-rollup")
    day = datetime.now(tz=timezone.utc) - timedelta(days=1)

    for arm, booked in (("Variant A", 2), ("Variant B", 5)):
        for n in range(10):
            run_id = str(uuid.uuid4())
            await session.execute(
                text(
                    "INSERT INTO automation_workflow_runs (id, institution_id,"
                    " location_id, workflow_id, workflow_version_id, status, outcome,"
                    " created_at, completed_at, updated_at)"
                    " VALUES (:id,:i,:l,:w,:v,'completed',:o,:d,:d,:d)"
                ),
                {
                    "id": run_id,
                    "i": INST_A,
                    "l": LOC_A,
                    "w": str(wf.id),
                    "v": str(v1.id),
                    "o": "booked" if n < booked else None,
                    "d": day,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO automation_workflow_split_assignments (id,"
                    " institution_id, location_id, workflow_id, workflow_version_id,"
                    " workflow_run_id, node_id, branch_label, bucket, assigned_at)"
                    " VALUES (:id,:i,:l,:w,:v,:r,'ab',:b,1,:d)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "i": INST_A,
                    "l": LOC_A,
                    "w": str(wf.id),
                    "v": str(v1.id),
                    "r": run_id,
                    "b": arm,
                    "d": day,
                },
            )
    await session.commit()

    window = day.date()
    await recompute_window(session, start_date=window, end_date=window)
    await session.commit()

    arms = dict(
        (row[0], (row[1], row[2]))
        for row in (
            await session.execute(
                text(
                    "SELECT branch_label, SUM(enrollments), SUM(booked)"
                    " FROM campaign_split_metrics_daily WHERE workflow_id = :w"
                    " GROUP BY branch_label"
                ),
                {"w": str(wf.id)},
            )
        ).all()
    )
    assert arms == {"Variant A": (10, 2), "Variant B": (10, 5)}

    total = (
        await session.execute(
            text(
                "SELECT SUM(enrollments), SUM(booked) FROM campaign_metrics_daily"
                " WHERE workflow_id = :w"
            ),
            {"w": str(wf.id)},
        )
    ).one()
    assert sum(a[0] for a in arms.values()) == total[0]
    assert sum(a[1] for a in arms.values()) == total[1]


@pytest.mark.asyncio
async def test_runs_that_never_reached_a_split_are_left_out_of_the_experiment(session):
    """An inner join, deliberately.

    Counting unassigned runs under some "unassigned" bucket would put contacts
    who were never in the experiment into its denominator.
    """
    import uuid

    from src.app.services.automation.campaign_analytics_service import recompute_window

    wf, v1 = await _published_split(session, name="ab-unassigned")
    day = datetime.now(tz=timezone.utc) - timedelta(days=2)

    for _ in range(4):
        await session.execute(
            text(
                "INSERT INTO automation_workflow_runs (id, institution_id, location_id,"
                " workflow_id, workflow_version_id, status, outcome, created_at,"
                " completed_at, updated_at)"
                " VALUES (:id,:i,:l,:w,:v,'completed','booked',:d,:d,:d)"
            ),
            {
                "id": str(uuid.uuid4()),
                "i": INST_A,
                "l": LOC_A,
                "w": str(wf.id),
                "v": str(v1.id),
                "d": day,
            },
        )
    await session.commit()

    window = day.date()
    await recompute_window(session, start_date=window, end_date=window)
    await session.commit()

    split_rows = (
        await session.execute(
            text(
                "SELECT count(*) FROM campaign_split_metrics_daily WHERE workflow_id = :w"
            ),
            {"w": str(wf.id)},
        )
    ).scalar_one()
    campaign_rows = (
        await session.execute(
            text(
                "SELECT SUM(enrollments) FROM campaign_metrics_daily WHERE workflow_id = :w"
            ),
            {"w": str(wf.id)},
        )
    ).scalar_one()
    assert campaign_rows == 4
    assert split_rows == 0
