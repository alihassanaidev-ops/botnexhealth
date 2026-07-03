"""Unit tests for Plan 09 data layer: appointment trigger, recall scanner, bulk enroll."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.appointment_trigger_service import (
    AppointmentTriggerService,
    compute_enrollment_eta,
    make_appointment_idempotency_key,
    make_recall_idempotency_key,
)
from src.app.models.automation_workflow import AutomationWorkflowStatus
from src.app.tasks.automation_workflow import (
    _enroll_and_start_async,
    _trigger_appointment_async,
    _scan_recall_async,
    _recall_is_due,
    _recall_patient_id,
)
from src.app.api.routes.automation_workflows import (
    BulkEnrollRequest,
    BulkEnrollItem,
    bulk_enroll,
)

_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
_FUTURE_APPT = datetime(2026, 7, 11, 10, 0, 0, tzinfo=timezone.utc)  # 22h from _NOW


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(trigger_type="appointment_offset", version_id="ver-1", offset_hours=-24):
    wf = MagicMock()
    wf.id = "wf-1"
    wf.institution_id = "inst-1"
    wf.status = AutomationWorkflowStatus.ACTIVE.value
    wf.trigger_type = trigger_type
    wf.current_version_id = version_id
    wf.definition = {
        "trigger": {"type": trigger_type, "offset_hours": offset_hours},
        "entry_node_id": "exit-1",
        "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
    }
    return wf


def _make_session(workflows=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.flush = AsyncMock()

    result = MagicMock()
    result.scalars.return_value.all.return_value = workflows or []
    result.scalar_one_or_none.return_value = None
    result.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# compute_enrollment_eta
# ---------------------------------------------------------------------------


def test_compute_enrollment_eta_past_window_returns_none():
    wf = _make_workflow(offset_hours=-24)
    # appt was 10h ago; offset=-24h → eta = appt - 24h = 34h ago → past → None
    past_appt = datetime.now(tz=timezone.utc) - timedelta(hours=10)
    result = compute_enrollment_eta(wf, past_appt)
    assert result is None


def test_compute_enrollment_eta_future_offset():
    wf = _make_workflow(offset_hours=-2)
    # appt 22h from now; offset=-2h → eta = appt - 2h = 20h from now → future → valid
    with patch(
        "src.app.services.automation.appointment_trigger_service.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = _NOW
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = compute_enrollment_eta(wf, _FUTURE_APPT)
    expected = _FUTURE_APPT + timedelta(hours=-2)
    assert result == expected


def test_compute_enrollment_eta_no_definition():
    wf = MagicMock()
    wf.definition = None
    assert compute_enrollment_eta(wf, _FUTURE_APPT) is None


def test_compute_enrollment_eta_wrong_trigger_type():
    wf = MagicMock()
    wf.definition = {
        "trigger": {"type": "recall_scan", "recall_interval_months": 6},
        "entry_node_id": "e",
        "nodes": [{"type": "exit", "id": "e"}],
    }
    assert compute_enrollment_eta(wf, _FUTURE_APPT) is None


# ---------------------------------------------------------------------------
# make_appointment_idempotency_key
# ---------------------------------------------------------------------------


def test_make_appointment_idempotency_key_format():
    key = make_appointment_idempotency_key("ver-abc", "appt-123")
    assert key == "appt:ver-abc:appt-123"


def test_make_appointment_idempotency_key_stable():
    a = make_appointment_idempotency_key("ver-1", "appt-1")
    b = make_appointment_idempotency_key("ver-1", "appt-1")
    assert a == b


# ---------------------------------------------------------------------------
# AppointmentTriggerService.find_active_appointment_workflows
# ---------------------------------------------------------------------------


def test_find_active_appointment_workflows_queries_db():
    wf = _make_workflow()
    session = _make_session(workflows=[wf])

    async def run():
        svc = AppointmentTriggerService(session)
        return await svc.find_active_appointment_workflows("inst-1")

    results = asyncio.run(run())
    assert len(results) == 1
    session.execute.assert_awaited_once()


def test_find_active_recall_workflows_queries_db():
    wf = _make_workflow(trigger_type="recall_scan")
    session = _make_session(workflows=[wf])

    async def run():
        svc = AppointmentTriggerService(session)
        return await svc.find_active_recall_workflows("inst-1")

    results = asyncio.run(run())
    assert len(results) == 1


# ---------------------------------------------------------------------------
# _trigger_appointment_async — no matching workflows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_appointment_no_workflows():
    mock_session = _make_session(workflows=[])

    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=mock_session,
    ), patch(
        "src.app.tasks.automation_workflow.AppointmentTriggerService"
    ) as MockSvc:
        instance = AsyncMock()
        instance.find_active_appointment_workflows = AsyncMock(return_value=[])
        MockSvc.return_value = instance

        result = await _trigger_appointment_async(
            institution_id="inst-1",
            appointment_id="appt-1",
            appointment_at_iso=_FUTURE_APPT.isoformat(),
            contact_id=None,
            location_id=None,
            trigger_metadata={},
        )

    assert result["scheduled"] == 0
    assert result["appointment_id"] == "appt-1"


# ---------------------------------------------------------------------------
# make_recall_idempotency_key
# ---------------------------------------------------------------------------


def test_make_recall_idempotency_key_format():
    key = make_recall_idempotency_key("ver-1", "pat-9", "2026-07")
    assert key == "recall:ver-1:pat-9:2026-07"


# ---------------------------------------------------------------------------
# recall due-date / patient-id helpers
# ---------------------------------------------------------------------------


def test_recall_patient_id_direct_and_nested():
    assert _recall_patient_id({"patient_id": 42}) == "42"
    assert _recall_patient_id({"patient": {"id": 7}}) == "7"
    assert _recall_patient_id({}) is None


def test_recall_is_due():
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert _recall_is_due({"due_date": "2026-06-01"}, now=now) is True  # overdue
    assert _recall_is_due({"due_date": "2026-09-01"}, now=now) is False  # future
    assert _recall_is_due({}, now=now) is True  # no due date → treated as due


# ---------------------------------------------------------------------------
# _scan_recall_async — no active recall workflows → empty summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_recall_async_no_workflows_returns_empty_summary():
    mock_session = _make_session(workflows=[])

    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=mock_session,
    ):
        result = await _scan_recall_async()

    assert result["active_recall_workflows"] == 0
    assert result["institutions"] == 0
    assert result["enrolled"] == 0


# ---------------------------------------------------------------------------
# _scan_recall_async — enrolls due patients from the NexHealth recall list
# ---------------------------------------------------------------------------


def _cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_scan_recall_async_enrolls_due_patients():
    # One active recall workflow for one institution.
    wf = _make_workflow(trigger_type="recall_scan", version_id="ver-1")
    scan_session = _make_session(workflows=[wf])

    # Institution-scoped session: get(Institution) truthy, one configured location,
    # then a contact lookup per due patient (all None → contact_id None but still enrolls).
    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=MagicMock())  # Institution present
    location = MagicMock()
    location.id = "loc-1"
    location.nexhealth_subdomain = "sub"
    location.nexhealth_location_id = "nexloc-1"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    contact_result = MagicMock()
    contact_result.scalar_one_or_none.return_value = None
    # 1 locations query + 3 contact lookups (one per due recall)
    inst_session.execute = AsyncMock(
        side_effect=[loc_result, contact_result, contact_result, contact_result]
    )

    recalls = [
        {"patient_id": "p1", "due_date": "2020-01-01"},
        {"patient_id": "p2", "due_date": "2020-01-01"},
        {"patient_id": "p3", "due_date": "2020-01-01"},
    ]
    adapter = AsyncMock()
    adapter.list_patient_recalls = AsyncMock(return_value=recalls)
    adapter.close = AsyncMock()

    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        side_effect=[_cm(scan_session), _cm(inst_session)],
    ), patch(
        "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
        AsyncMock(return_value=adapter),
    ), patch(
        "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
    ) as mock_task:
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    # 3 due patients × 1 workflow = 3 enrollment tasks queued.
    assert result["enrolled"] == 3
    assert result["institutions"] == 1
    assert mock_task.apply_async.call_count == 3

    # Idempotency keys follow recall:{version}:{patient}:{period}.
    keys = {c.kwargs["kwargs"]["idempotency_key"] for c in mock_task.apply_async.call_args_list}
    assert all(k.startswith("recall:ver-1:") for k in keys)
    assert len(keys) == 3  # distinct per patient
    # Recall runs carry the recall trigger ref type.
    assert all(
        c.kwargs["kwargs"]["trigger_ref_type"] == "recall"
        for c in mock_task.apply_async.call_args_list
    )


# ---------------------------------------------------------------------------
# _enroll_and_start_async — duplicate idempotency key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_and_start_duplicate_key_skips():
    existing_run = MagicMock()
    existing_run.id = "run-existing"

    mock_enroll_svc = AsyncMock()
    mock_enroll_svc.enroll = AsyncMock(return_value=(existing_run, False))

    mock_session = _make_session()

    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=mock_session,
    ), patch(
        "src.app.tasks.automation_workflow.AutomationWorkflowEnrollmentService",
        return_value=mock_enroll_svc,
    ):
        result = await _enroll_and_start_async(
            institution_id="inst-1",
            workflow_id="wf-1",
            workflow_version_id="ver-1",
            contact_id="c-1",
            location_id=None,
            trigger_type="appointment_offset",
            trigger_ref_type="appointment",
            trigger_ref_id="appt-1",
            idempotency_key="appt:ver-1:appt-1",
            trigger_metadata={},
        )

    assert result["created"] is False
    assert result["run_id"] == "run-existing"


# ---------------------------------------------------------------------------
# bulk_enroll route — enqueues tasks and returns 202
# ---------------------------------------------------------------------------


def test_bulk_enroll_enqueues_tasks():

    user = MagicMock()
    user.institution_id = "inst-1"

    wf = MagicMock()
    wf.id = "wf-1"
    wf.status = "active"
    wf.current_version_id = "ver-1"
    wf.trigger_type = "manual"

    mock_svc = AsyncMock()
    mock_svc.get_workflow = AsyncMock(return_value=wf)
    mock_session = _make_session()

    data = BulkEnrollRequest(items=[
        BulkEnrollItem(contact_id="c-1", idempotency_key="k-1"),
        BulkEnrollItem(contact_id="c-2", idempotency_key="k-2"),
    ])

    with patch(
        "src.app.api.routes.automation_workflows.get_db_session",
        return_value=mock_session,
    ), patch(
        "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
        return_value=mock_svc,
    ), patch(
        "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
    ) as mock_task:
        mock_task.apply_async = MagicMock()
        result = asyncio.run(bulk_enroll("wf-1", data, user))

    assert result.enqueued == 2
    assert mock_task.apply_async.call_count == 2


def test_bulk_enroll_rejects_inactive_workflow():
    from fastapi import HTTPException

    user = MagicMock()
    user.institution_id = "inst-1"

    wf = MagicMock()
    wf.status = "draft"
    wf.current_version_id = "ver-1"

    mock_svc = AsyncMock()
    mock_svc.get_workflow = AsyncMock(return_value=wf)
    mock_session = _make_session()

    data = BulkEnrollRequest(items=[BulkEnrollItem(contact_id="c-1", idempotency_key="k-1")])

    with patch(
        "src.app.api.routes.automation_workflows.get_db_session",
        return_value=mock_session,
    ), patch(
        "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
        return_value=mock_svc,
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(bulk_enroll("wf-1", data, user))

    assert exc_info.value.status_code == 409
