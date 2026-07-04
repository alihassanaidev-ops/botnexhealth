"""End-to-end integration tests for the workflow engine against a REAL Postgres.

These exercise the DB-backed mechanics that mock-session unit tests cannot prove:
immutable version pinning, the durable timer wait→resume→exit cycle, crashed-worker
stale-claim recovery, the emergency-halt run+timer cascade, the idempotency unique
index under a real constraint, and RLS isolation of automation runs across tenants.

Uses testcontainers Postgres (skips if Docker/testcontainers unavailable) and runs
the real Alembic chain to head, mirroring tests/integration/test_rls_postgres.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = [pytest.mark.integration, pytest.mark.rls]

ROOT = Path(__file__).resolve().parents[2]

INST_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
INST_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
LOC_A = "11111111-1111-1111-1111-111111111111"
LOC_B = "33333333-3333-3333-3333-333333333333"


# ---------------------------------------------------------------------------
# Container + schema fixtures (mirror test_rls_postgres.py)
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
        raw = container.get_connection_url()
        yield _asyncpg(raw)
    finally:
        container.stop()


def _asyncpg(url: str) -> str:
    for prefix in ("postgresql+psycopg2://", "postgresql://"):
        if url.startswith(prefix):
            return url.replace(prefix, "postgresql+asyncpg://", 1)
    return url


def _with_creds(url: str, user: str, pw: str) -> str:
    return make_url(url).set(username=user, password=pw).render_as_string(hide_password=False)


def _upgrade_head(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


async def _set_ctx(conn, *, context_type="celery", institution_id="", location_id="",
                   role="", user_id="", external_id="wf-int-test", group_id="") -> None:
    values = {
        "app.context_type": context_type, "app.user_id": user_id, "app.role": role,
        "app.institution_id": institution_id, "app.location_id": location_id,
        "app.external_id": external_id, "app.group_id": group_id,
    }
    for k, v in values.items():
        await conn.execute(text("SELECT set_config(:k, :v, false)"), {"k": k, "v": v})


async def _seed_tenants(conn) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO institutions (id, name, slug, is_active) VALUES
              (:a, 'Clinic A', 'clinic-a', true),
              (:b, 'Clinic B', 'clinic-b', true)
            ON CONFLICT DO NOTHING
            """
        ),
        {"a": INST_A, "b": INST_B},
    )
    await conn.execute(
        text(
            """
            INSERT INTO institution_locations
              (id, institution_id, name, slug, is_active, retell_agent_id,
               twilio_from_number, timezone) VALUES
              (:la, :a, 'A One', 'a-one', true, 'agent-a', '+15550000001', 'UTC'),
              (:lb, :b, 'B One', 'b-one', true, 'agent-b', '+15550000003', 'UTC')
            ON CONFLICT DO NOTHING
            """
        ),
        {"la": LOC_A, "lb": LOC_B, "a": INST_A, "b": INST_B},
    )


@pytest_asyncio.fixture(scope="module")
async def superuser_engine(pg_url: str):
    """Superuser engine (RLS-bypassing) for seeding + driving engine behavior."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WAIT_EXIT_DEF = {
    "trigger": {"type": "manual"},
    "entry_node_id": "w1",
    "nodes": [
        {"type": "wait", "id": "w1",
         "delay": {"delay_type": "duration", "duration_seconds": 0}, "next_node_id": "x1"},
        {"type": "exit", "id": "x1", "outcome": "done"},
    ],
}


async def _make_published_workflow(session, definition=None, *, name="wf"):
    from src.app.services.automation.definition_service import AutomationWorkflowDefinitionService

    svc = AutomationWorkflowDefinitionService(session)
    wf = await svc.create_draft(INST_A, name=name, location_id=LOC_A)
    version = await svc.publish_version(wf, definition or _WAIT_EXIT_DEF)
    await session.commit()
    return wf, version


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_immutability_and_version_pinning(session):
    """A run enrolled under v1 stays pinned to v1 even after v2 is published."""
    from src.app.services.automation.definition_service import AutomationWorkflowDefinitionService
    from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService

    svc = AutomationWorkflowDefinitionService(session)
    wf, v1 = await _make_published_workflow(session)

    enroll = AutomationWorkflowEnrollmentService(session)
    run, created = await enroll.enroll(
        institution_id=INST_A, workflow_id=str(wf.id),
        workflow_version_id=str(v1.id), location_id=LOC_A, idempotency_key="pin-1",
    )
    await session.commit()
    assert created is True

    # Publish v2 (workflow must be paused first — publishable statuses are draft/paused).
    await svc.pause_workflow(wf)
    v2 = await svc.publish_version(wf, _WAIT_EXIT_DEF)
    await session.commit()

    assert v2.version_number == v1.version_number + 1
    assert str(wf.current_version_id) == str(v2.id)
    assert str(run.workflow_version_id) == str(v1.id)  # run pinned to enrolled version


@pytest.mark.asyncio
async def test_enroll_wait_resume_exit_cycle(session):
    """Full durable cycle: enroll → advance to WAITING (timer row) → resume → exit."""
    from src.app.models.automation_workflow import AutomationRunStatus
    from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
    from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
    from src.app.services.automation.step_dispatcher import build_dispatcher

    wf, v1 = await _make_published_workflow(session)
    enroll = AutomationWorkflowEnrollmentService(session)
    run, _ = await enroll.enroll(
        institution_id=INST_A, workflow_id=str(wf.id),
        workflow_version_id=str(v1.id), location_id=LOC_A, idempotency_key="cyc-1",
    )
    runtime = AutomationWorkflowRuntimeService(session)
    await runtime.start_run(run)

    dispatcher, tz = await build_dispatcher(session, location_id=LOC_A)
    from src.app.services.automation.definition_schema import WorkflowDefinition
    definition = WorkflowDefinition.model_validate(_WAIT_EXIT_DEF)

    r1 = await dispatcher.advance(run, definition, context={}, location_timezone=tz)
    await session.commit()
    assert r1.status == "waiting"
    assert run.status == AutomationRunStatus.WAITING.value
    assert r1.timer_id is not None

    # Resume as the fired-timer path would.
    r2 = await dispatcher.resume_after_timer(run, definition, context={}, location_timezone=tz)
    await session.commit()
    assert r2.status == "completed"
    assert r2.outcome == "done"
    assert run.status == AutomationRunStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_stale_claim_recovery(session):
    """A timer claimed by a since-crashed worker is returned to PENDING (A12)."""
    from datetime import datetime, timedelta, timezone

    from src.app.models.automation_workflow import AutomationTimerStatus
    from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
    from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService

    wf, v1 = await _make_published_workflow(session)
    enroll = AutomationWorkflowEnrollmentService(session)
    run, _ = await enroll.enroll(
        institution_id=INST_A, workflow_id=str(wf.id),
        workflow_version_id=str(v1.id), location_id=LOC_A, idempotency_key="stale-1",
    )
    sched = AutomationWorkflowSchedulerService(session)
    now = datetime.now(tz=timezone.utc)
    timer = await sched.create_timer(
        institution_id=INST_A, location_id=LOC_A, workflow_run_id=str(run.id),
        due_at=now - timedelta(seconds=10),
    )
    await session.commit()

    claimed = await sched.claim_due_timers(now=now)
    assert any(t.id == timer.id for t in claimed)
    assert timer.status == AutomationTimerStatus.CLAIMED.value

    # Simulate the claiming worker dying: expire the claim, then recover.
    timer.claim_expires_at = now - timedelta(seconds=1)
    await session.flush()
    recovered = await sched.recover_stale_claims(now=now)
    await session.commit()
    assert recovered >= 1
    assert timer.status == AutomationTimerStatus.PENDING.value


@pytest.mark.asyncio
async def test_emergency_halt_cascades_to_runs_and_timers(session):
    """emergency_halt_version cancels in-flight runs AND their pending timers (A4)."""
    from src.app.models.automation_workflow import AutomationRunStatus, AutomationTimerStatus
    from src.app.services.automation.definition_service import AutomationWorkflowDefinitionService
    from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
    from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService

    wf, v1 = await _make_published_workflow(session)
    enroll = AutomationWorkflowEnrollmentService(session)
    run, _ = await enroll.enroll(
        institution_id=INST_A, workflow_id=str(wf.id),
        workflow_version_id=str(v1.id), location_id=LOC_A, idempotency_key="halt-1",
    )
    from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
    await AutomationWorkflowRuntimeService(session).start_run(run)
    sched = AutomationWorkflowSchedulerService(session)
    from datetime import datetime, timedelta, timezone
    timer = await sched.create_timer(
        institution_id=INST_A, location_id=LOC_A, workflow_run_id=str(run.id),
        due_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
    )
    await session.commit()

    svc = AutomationWorkflowDefinitionService(session)
    halted = await svc.emergency_halt_version(
        institution_id=INST_A, workflow_version_id=str(v1.id), actor_user_id=None
    )
    await session.commit()
    assert halted >= 1
    await session.refresh(run)
    await session.refresh(timer)
    assert run.status == AutomationRunStatus.CANCELLED.value
    assert timer.status == AutomationTimerStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_idempotency_enforced_by_unique_index(session):
    """Enrolling twice with the same key returns the same run (real unique index)."""
    from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService

    wf, v1 = await _make_published_workflow(session)
    enroll = AutomationWorkflowEnrollmentService(session)
    run1, created1 = await enroll.enroll(
        institution_id=INST_A, workflow_id=str(wf.id),
        workflow_version_id=str(v1.id), location_id=LOC_A, idempotency_key="dup-1",
    )
    await session.commit()
    run2, created2 = await enroll.enroll(
        institution_id=INST_A, workflow_id=str(wf.id),
        workflow_version_id=str(v1.id), location_id=LOC_A, idempotency_key="dup-1",
    )
    await session.commit()
    assert created1 is True
    assert created2 is False
    assert str(run1.id) == str(run2.id)


@pytest.mark.asyncio
async def test_send_step_idempotency_and_reclaim_after_hold(session):
    """XC-1 (real unique index): begin_step re-entry on the same send node — as
    happens when a quiet-hours hold resumes — must NOT collide on
    uq_automation_step_execution_attempt, and already_sent must detect a completed
    send so a redelivery would skip re-contacting the patient."""
    from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
    from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService

    wf, v1 = await _make_published_workflow(session)
    enroll = AutomationWorkflowEnrollmentService(session)
    run, _ = await enroll.enroll(
        institution_id=INST_A, workflow_id=str(wf.id),
        workflow_version_id=str(v1.id), location_id=LOC_A, idempotency_key="xc1-1",
    )
    runtime = AutomationWorkflowRuntimeService(session)
    await runtime.start_run(run)

    # 1st begin_step for the send node = attempt 1 (a quiet-hours hold creates this).
    hold_step = await runtime.begin_step(run, step_id="n-send", step_type="send_sms")
    assert hold_step.attempt_number == 1
    # resume_run marks the hold step COMPLETED with no result_code (not a send).
    await runtime.complete_step(hold_step, result_code=None)
    await session.commit()
    assert await runtime.already_sent(run, "n-send") is False

    # 2nd begin_step for the SAME node must NOT raise a unique-index violation —
    # it auto-increments to attempt 2 (the latent hold->resume collision fix).
    send_step = await runtime.begin_step(run, step_id="n-send", step_type="send_sms")
    assert send_step.attempt_number == 2
    await runtime.complete_step(send_step, result_code="sent")
    await session.commit()

    # A redelivery / re-advance would now see the completed send and skip re-sending.
    assert await runtime.already_sent(run, "n-send") is True


_VOICE_OUTCOME_DEF = {
    "trigger": {"type": "manual"},
    "entry_node_id": "v1",
    "nodes": [
        {"type": "send_voice", "id": "v1", "retell_agent_id": "agent_1",
         "next_node_id": "c1", "wait_for_outcome": True},
        {"type": "condition", "id": "c1", "logic": "AND",
         "rules": [{"field": "call_outcome", "op": "eq", "value": "answered"}],
         "true_next_node_id": "x_done", "false_next_node_id": "x_other"},
        {"type": "exit", "id": "x_done", "outcome": "answered"},
        {"type": "exit", "id": "x_other", "outcome": "other"},
    ],
}


@pytest.mark.asyncio
async def test_voice_wait_for_outcome_resume_advances_and_branches(session):
    """Plan 03 outcome loop: a voice node parked WAITING resumes on the outcome,
    advances PAST the send node (does NOT re-dial), and a ConditionNode branches on
    the `call_outcome` written into run context."""
    from src.app.models.automation_workflow import AutomationRunStatus, AutomationStepStatus
    from src.app.services.automation.definition_schema import WorkflowDefinition
    from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
    from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
    from src.app.services.automation.step_dispatcher import build_dispatcher

    wf, v1 = await _make_published_workflow(session, _VOICE_OUTCOME_DEF, name="voice-outcome")
    enroll = AutomationWorkflowEnrollmentService(session)
    run, _ = await enroll.enroll(
        institution_id=INST_A, workflow_id=str(wf.id),
        workflow_version_id=str(v1.id), location_id=LOC_A, idempotency_key="voc-1",
    )
    runtime = AutomationWorkflowRuntimeService(session)
    await runtime.start_run(run)

    # Simulate the executor having placed the call and parked the run.
    step = await runtime.begin_step(run, step_id="v1", step_type="send_voice")
    await runtime.mark_step_awaiting_outcome(
        step, result_code="call_placed_awaiting_outcome",
        result_metadata={"retell_call_id": "call_1"},
    )
    await runtime.wait_run(run, step)
    await session.commit()
    assert run.status == AutomationRunStatus.WAITING.value

    # Resume with outcome=answered → advance past v1, branch true → exit "answered".
    md = {"call_outcome": "answered"}
    run.trigger_metadata = md
    await session.flush()
    dispatcher, tz = await build_dispatcher(session, location_id=LOC_A)
    definition = WorkflowDefinition.model_validate(_VOICE_OUTCOME_DEF)
    result = await dispatcher.resume_after_timer(run, definition, context=md, location_timezone=tz)
    await session.commit()

    assert result.status == "completed"
    assert result.outcome == "answered"  # branched on call_outcome, did not re-dial
    assert run.status == AutomationRunStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_voice_consent_capture_is_channel_scoped(session):
    """XC-6 (real DB): the channel-generic consent writer + `has_consent_record` let a
    granted VOICE consent be recorded and detected, scoped to the VOICE channel only —
    this is what unblocks the voice gate for AI callbacks."""
    from src.app.models.sms_consent import ConsentChannel, ConsentStatus
    from src.app.services.sms_compliance import SmsComplianceService

    comp = SmsComplianceService(session)
    phone = "+14165551234"

    assert await comp.has_consent_record(INST_A, phone, ConsentChannel.VOICE) is False
    await comp.record_consent(
        institution_id=INST_A,
        phone=phone,
        status=ConsentStatus.GRANTED,
        channel=ConsentChannel.VOICE,
        source="system",
        reason="inbound_callback_request",
    )
    await session.commit()

    assert await comp.has_consent_record(INST_A, phone, ConsentChannel.VOICE) is True
    # Channel-scoped: recording VOICE consent does NOT imply SMS consent.
    assert await comp.has_consent_record(INST_A, phone, ConsentChannel.SMS) is False


@pytest.mark.asyncio
async def test_rls_isolates_runs_across_institutions(pg_url, superuser_engine):
    """As the non-superuser app role under a celery/INST_A context, only INST_A's
    runs are visible — cross-tenant automation runs are RLS-isolated."""
    from sqlalchemy import select

    from src.app.models.automation_workflow import AutomationWorkflowRun

    # Seed one workflow+version+run in each institution as superuser.
    from src.app.services.automation.definition_service import AutomationWorkflowDefinitionService
    from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService

    maker = async_sessionmaker(superuser_engine, expire_on_commit=False)
    async with maker() as s:
        conn = await s.connection()
        for inst, loc, key in ((INST_A, LOC_A, "rls-a"), (INST_B, LOC_B, "rls-b")):
            await _set_ctx(conn, institution_id=inst, location_id=loc)
            svc = AutomationWorkflowDefinitionService(s)
            wf = await svc.create_draft(inst, name=f"rls-{inst[:4]}", location_id=loc)
            v = await svc.publish_version(wf, _WAIT_EXIT_DEF)
            await AutomationWorkflowEnrollmentService(s).enroll(
                institution_id=inst, workflow_id=str(wf.id),
                workflow_version_id=str(v.id), location_id=loc, idempotency_key=key,
            )
        await s.commit()

    # Ensure the app role can be created and RLS applies (mirror rls harness).
    admin_engine = create_async_engine(pg_url, poolclass=NullPool)
    try:
        async with admin_engine.begin() as c:
            exists = (await c.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname='rls_app'")
            )).scalar()
            if not exists:
                await c.execute(text("CREATE ROLE rls_app LOGIN PASSWORD 'rls_app'"))
                await c.execute(text("GRANT USAGE ON SCHEMA public TO rls_app"))
                await c.execute(text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rls_app"
                ))
                await c.execute(text(
                    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rls_app"
                ))
    finally:
        await admin_engine.dispose()

    app_engine = create_async_engine(
        _with_creds(pg_url, "rls_app", "rls_app"), poolclass=NullPool
    )
    try:
        async with app_engine.connect() as conn:
            await _set_ctx(conn, context_type="celery", institution_id=INST_A, location_id=LOC_A)
            rows = (await conn.execute(select(AutomationWorkflowRun.institution_id))).scalars().all()
        assert rows, "expected at least INST_A's run visible"
        assert all(str(r) == INST_A for r in rows), "cross-tenant runs leaked under RLS"
    finally:
        await app_engine.dispose()
