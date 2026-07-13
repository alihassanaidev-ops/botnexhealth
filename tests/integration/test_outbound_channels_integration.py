"""End-to-end integration tests for the outbound channels + compliance gate
against a REAL Postgres (QA Layer 2).

These convert mock-only unit coverage into durable E2E proof that, driving the
real dispatcher/executors through the real DB:

  1. an SMS send writes an sms_history_logs row STAMPED with workflow_run_id AND
     workflow_id (the Plan 04 + 11 attribution fix — previously NULL);
  2. a Do-Not-Contact row BLOCKS the send at the compliance gate (Plan 12);
  3. a REVOKED EMAIL consent record (Plan 05 suppression, keyed on email_hash)
     makes the gate block a subsequent email send to that identity;
  4. the voice node passes metadata carrying workflow_id to Retell (Plan 03 + 11).

Only the vendor HTTP boundary is stubbed (Twilio Client / Retell create_phone_call);
everything else — enrollment, dispatch, gate, logging — runs against real Postgres.

Mirrors tests/integration/test_automation_engine_integration.py: testcontainers
Postgres, the real Alembic chain to head, the RLS-bypassing superuser session
seeded to INST_A/LOC_A, and the _make_published_workflow helper.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = [pytest.mark.integration, pytest.mark.rls]

ROOT = Path(__file__).resolve().parents[2]

INST_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
INST_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
LOC_A = "11111111-1111-1111-1111-111111111111"
LOC_B = "33333333-3333-3333-3333-333333333333"


# ---------------------------------------------------------------------------
# Container + schema fixtures (copied from test_automation_engine_integration.py —
# module-scoped fixtures cannot be shared across modules without a conftest, so
# each integration module carries its own copy, as the existing files do).
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
               retell_from_number, twilio_from_number, timezone) VALUES
              (:la, :a, 'A One', 'a-one', true, 'agent-a', '+15550000009', '+15550000001', 'UTC'),
              (:lb, :b, 'B One', 'b-one', true, 'agent-b', '+15550000004', '+15550000003', 'UTC')
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


async def _make_published_workflow(session, definition, *, name="wf"):
    from src.app.services.automation.definition_service import (
        AutomationWorkflowDefinitionService,
    )

    svc = AutomationWorkflowDefinitionService(session)
    wf = await svc.create_draft(INST_A, name=name, location_id=LOC_A)
    version = await svc.publish_version(wf, definition)
    await session.commit()
    return wf, version


async def _make_contact(session, *, phone=None, email=None, first_name="Pat"):
    from src.app.models.contact import Contact

    contact = Contact(institution_id=INST_A, first_name=first_name)
    if phone is not None:
        contact.phone = phone
    if email is not None:
        contact.email = email
    session.add(contact)
    await session.flush()
    return contact


def _send_sms_def(*, next_id="x1"):
    return {
        "trigger": {"type": "manual"},
        "entry_node_id": "s1",
        "nodes": [
            {"type": "send_sms", "id": "s1",
             "body_template": "Reminder for your visit.", "next_node_id": next_id},
            {"type": "exit", "id": next_id, "outcome": "sent"},
        ],
    }


def _send_voice_def(*, next_id="x1"):
    return {
        "trigger": {"type": "manual"},
        "entry_node_id": "v1",
        "nodes": [
            {"type": "send_voice", "id": "v1", "retell_agent_id": "agent-a",
             "next_node_id": next_id, "wait_for_outcome": False},
            {"type": "exit", "id": next_id, "outcome": "called"},
        ],
    }


def _send_email_def(*, next_id="x1"):
    return {
        "trigger": {"type": "manual"},
        "entry_node_id": "e1",
        "nodes": [
            {"type": "send_email", "id": "e1", "subject_template": "Your appointment",
             "body_template": "See you soon.", "next_node_id": next_id},
            {"type": "exit", "id": next_id, "outcome": "emailed"},
        ],
    }


async def _enroll_started(session, wf, version, *, contact_id, key):
    from src.app.services.automation.enrollment_service import (
        AutomationWorkflowEnrollmentService,
    )
    from src.app.services.automation.runtime_service import (
        AutomationWorkflowRuntimeService,
    )

    run, _ = await AutomationWorkflowEnrollmentService(session).enroll(
        institution_id=INST_A, workflow_id=str(wf.id),
        workflow_version_id=str(version.id), location_id=LOC_A,
        contact_id=contact_id, idempotency_key=key,
    )
    await AutomationWorkflowRuntimeService(session).start_run(run)
    return run


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sms_send_stamps_workflow_attribution(session):
    """Plan 04 + 11: a real SMS dispatch writes an sms_history_logs row carrying
    workflow_run_id == run.id AND workflow_id == run.workflow_id (the attribution
    fix — previously NULL), and the send step completes."""
    from src.app.models.automation_workflow import AutomationRunStatus
    from src.app.models.sms_history_log import SmsHistoryLog, SmsStatus
    from src.app.services.automation.definition_schema import WorkflowDefinition
    from src.app.services.automation.step_dispatcher import build_dispatcher

    contact = await _make_contact(session, phone="+14165551234")
    definition_dict = _send_sms_def()
    wf, v1 = await _make_published_workflow(session, definition_dict, name="sms-attr")
    run = await _enroll_started(session, wf, v1, contact_id=str(contact.id), key="sms-attr-1")

    dispatcher, tz = await build_dispatcher(session, location_id=LOC_A)
    definition = WorkflowDefinition.model_validate(definition_dict)

    fake_msg = MagicMock(sid="SM_fake_123", status="queued")
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_msg

    with patch(
        "src.app.services.sms_service.SmsService._get_twilio_client",
        return_value=fake_client,
    ):
        result = await dispatcher.advance(run, definition, context={}, location_timezone=tz)
    await session.commit()

    assert result.status == "completed"
    assert result.outcome == "sent"
    assert run.status == AutomationRunStatus.COMPLETED.value

    row = (
        await session.execute(
            select(SmsHistoryLog).where(SmsHistoryLog.workflow_run_id == str(run.id))
        )
    ).scalar_one()
    # The attribution fix: both ids stamped (previously NULL).
    assert str(row.workflow_run_id) == str(run.id)
    assert str(row.workflow_id) == str(run.workflow_id)
    assert row.status == SmsStatus.SENT.value
    fake_client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_sms_blocked_by_do_not_contact(session):
    """Plan 12: a Do-Not-Contact row for the contact/location BLOCKS the send at
    the compliance gate — the step fails compliance_blocked, the run fails, and no
    Twilio call is attempted."""
    from src.app.models.automation_workflow import (
        AutomationRunStatus,
        AutomationStepStatus,
        AutomationWorkflowStepExecution,
    )
    from src.app.models.sms_consent import DncScope, DoNotContact
    from src.app.models.sms_history_log import SmsHistoryLog
    from src.app.services.automation.definition_schema import WorkflowDefinition
    from src.app.services.automation.step_dispatcher import build_dispatcher
    from src.app.services.sms_privacy import hash_phone, mask_phone

    phone = "+14165557777"
    contact = await _make_contact(session, phone=phone)

    # DNC blocks all channels for this identity in the institution.
    session.add(
        DoNotContact(
            institution_id=INST_A, location_id=LOC_A, contact_id=str(contact.id),
            phone_hash=hash_phone(phone), phone_masked=mask_phone(phone),
            scope=DncScope.INSTITUTION.value, source="manual", reason="patient_request",
        )
    )
    await session.flush()

    definition_dict = _send_sms_def()
    wf, v1 = await _make_published_workflow(session, definition_dict, name="sms-dnc")
    run = await _enroll_started(session, wf, v1, contact_id=str(contact.id), key="sms-dnc-1")

    dispatcher, tz = await build_dispatcher(session, location_id=LOC_A)
    definition = WorkflowDefinition.model_validate(definition_dict)

    fake_client = MagicMock()
    with patch(
        "src.app.services.sms_service.SmsService._get_twilio_client",
        return_value=fake_client,
    ):
        result = await dispatcher.advance(run, definition, context={}, location_timezone=tz)
    await session.commit()

    # Blocked at the gate: run failed, no Twilio attempted.
    assert result.status == "failed"
    assert run.status == AutomationRunStatus.FAILED.value
    fake_client.messages.create.assert_not_called()

    # The send step recorded the compliance_blocked result.
    step = (
        await session.execute(
            select(AutomationWorkflowStepExecution).where(
                AutomationWorkflowStepExecution.workflow_run_id == run.id,
                AutomationWorkflowStepExecution.step_id == "s1",
            )
        )
    ).scalar_one()
    assert step.status == AutomationStepStatus.FAILED.value
    assert step.result_code == "compliance_blocked"

    # The gate blocked BEFORE SmsService, so no history row was written at all.
    rows = (
        await session.execute(
            select(SmsHistoryLog).where(SmsHistoryLog.workflow_run_id == str(run.id))
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_email_suppression_by_email_hash_blocks_gate(session):
    """Plan 05: a granted EMAIL ConsentRecord lets email through, but after the
    suppression path writes a REVOKED EMAIL consent keyed on the same email_hash
    (what _suppress_async does via record_email_consent_identity), the gate blocks
    a subsequent email send to that identity."""
    from src.app.models.sms_consent import (
        ConsentBasis,
        ConsentChannel,
        ConsentRecord,
        ConsentSource,
        ConsentStatus,
    )
    from src.app.services.automation.compliance_gate_service import ComplianceGateService
    from src.app.services.sms_compliance import SmsComplianceService
    from src.app.services.sms_privacy import hash_email

    email = "patient@example.com"
    email_hash = hash_email(email)
    contact = await _make_contact(session, email=email, first_name="Em")

    # Granted express-written EMAIL consent → gate allows email.
    session.add(
        ConsentRecord(
            institution_id=INST_A, channel=ConsentChannel.EMAIL.value,
            email_hash=email_hash, status=ConsentStatus.GRANTED.value,
            basis=ConsentBasis.EXPRESS_WRITTEN.value, source=ConsentSource.SYSTEM.value,
        )
    )
    await session.flush()

    definition_dict = _send_email_def()
    wf, v1 = await _make_published_workflow(session, definition_dict, name="email-suppress")
    run = await _enroll_started(session, wf, v1, contact_id=str(contact.id), key="email-sup-1")

    gate = ComplianceGateService(session)
    allowed = await gate.check(run, "send_email", content_class="marketing")
    assert allowed.action == "allow", f"expected allow before suppression, got {allowed}"

    # Suppression path (Plan 05 fix): resolve by email_hash and write a REVOKED
    # EMAIL consent — exactly what email_compliance._suppress_async does.
    await SmsComplianceService(session).record_email_consent_identity(
        institution_id=INST_A, email_hash=email_hash, status=ConsentStatus.REVOKED,
        source=ConsentSource.SYSTEM, reason="resend_email.bounced",
    )
    await session.flush()

    # A revoked record beats implied transactional consent → gate now blocks,
    # regardless of content class.
    blocked = await gate.check(run, "send_email", content_class="transactional_care")
    assert blocked.action == "block"
    assert "revoked" in (blocked.reason or "")


@pytest.mark.asyncio
async def test_voice_metadata_carries_workflow_id(session):
    """Plan 03 + 11: a real voice dispatch passes metadata to Retell carrying
    workflow_id == run.workflow_id (the attribution fix), with create_phone_call
    stubbed at the client boundary."""
    from src.app.config import settings
    from src.app.models.automation_workflow import AutomationRunStatus
    from src.app.services.automation.definition_schema import WorkflowDefinition
    from src.app.services.automation.retell_outbound_client import RetellCallResult
    from src.app.services.automation.step_dispatcher import build_dispatcher

    contact = await _make_contact(session, phone="+14165558888")
    definition_dict = _send_voice_def()
    wf, v1 = await _make_published_workflow(session, definition_dict, name="voice-attr")
    run = await _enroll_started(session, wf, v1, contact_id=str(contact.id), key="voice-attr-1")

    dispatcher, tz = await build_dispatcher(session, location_id=LOC_A)
    definition = WorkflowDefinition.model_validate(definition_dict)

    captured: dict = {}

    async def _fake_create_phone_call(**kwargs):
        captured.update(kwargs)
        return RetellCallResult(call_id="call_fake_1", call_status="registered")

    # RETELL_API_SECRET must be set or the executor fails "retell_not_configured".
    with patch.object(settings, "retell_api_secret", "test-retell-key"), patch(
        "src.app.services.automation.voice_node_executor.RetellOutboundClient.create_phone_call",
        new=AsyncMock(side_effect=_fake_create_phone_call),
    ):
        result = await dispatcher.advance(run, definition, context={}, location_timezone=tz)
    await session.commit()

    # Fire-and-forget voice node advanced to exit.
    assert result.status == "completed"
    assert run.status == AutomationRunStatus.COMPLETED.value

    metadata = captured.get("metadata")
    assert metadata is not None, "create_phone_call was not called with metadata"
    assert metadata["workflow_id"] == str(run.workflow_id)
    assert metadata["workflow_run_id"] == str(run.id)
    assert metadata["workflow_step_id"] == "v1"
