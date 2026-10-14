"""Unit tests for Plan 09 data layer: appointment trigger, recall scanner, bulk enroll."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.pms.models import UniversalRecallType, UniversalTreatmentPlan
from src.app.services.automation.appointment_trigger_service import (
    AppointmentTriggerService,
    compute_enrollment_eta,
    make_appointment_idempotency_key,
    make_recall_idempotency_key,
    workflow_matches_appointment,
)
from src.app.services.automation.trigger_lookup import TRIGGER_EVENT_KEYS
from src.app.models.automation_workflow import AutomationRunStatus, AutomationWorkflowStatus
from src.app.tasks.automation_workflow import (
    _enroll_and_start_async,
    _trigger_appointment_async,
    _scan_recall_async,
    _recall_is_due,
    _recall_patient_id,
    _recall_type_allowed,
    _recall_scan_should_alert,
    _LiveRecallPatient,
    _refresh_recall_contact_live,
)
from src.app.api.routes.automation_workflows import (
    BulkEnrollRequest,
    BulkEnrollItem,
    bulk_enroll,
)

_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
_LIVE_PATIENT = _LiveRecallPatient(
    contact_id="contact-live", inactive=False, last_visit_date=None
)


@pytest.fixture(autouse=True)
def _live_recall_refresh():
    """Stub the scanner's live NexHealth patient read; tests override it."""
    with patch(
        "src.app.tasks.automation_workflow._refresh_recall_contact_live",
        AsyncMock(return_value=_LIVE_PATIENT),
    ) as refresh:
        yield refresh
_FUTURE_APPT = datetime(2026, 7, 11, 10, 0, 0, tzinfo=timezone.utc)  # 22h from _NOW


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(
    trigger_type="appointment_offset",
    version_id="ver-1",
    offset_hours=-24,
    appointment_type_ids=None,
    location_id=None,
    trigger_filter=None,
    pms_context_fields=None,
    recall_source=None,
):
    wf = MagicMock()
    wf.id = "wf-1"
    wf.institution_id = "inst-1"
    wf.location_id = location_id
    wf.status = AutomationWorkflowStatus.ACTIVE.value
    wf.current_version_id = version_id
    if recall_source is not None:
        # Campaign-scoped recall settings live on the schedule source.
        trigger = {
            "type": "schedule",
            "cron": "0 9 * * *",
            "source": {
                "kind": "pms_recall",
                "recall_interval_months": 6,
                **recall_source,
            },
        }
    elif trigger_type == "recall_scan":
        trigger = {"type": trigger_type, "recall_interval_months": 6}
    else:
        trigger = {"type": trigger_type, "offset_hours": offset_hours}
    if trigger_filter is not None:
        trigger["filter"] = trigger_filter
    if appointment_type_ids is not None:
        trigger["appointment_type_ids"] = appointment_type_ids
    wf.definition = {
        "trigger": trigger,
        "entry_node_id": "exit-1",
        "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
    }
    if pms_context_fields is not None:
        wf.definition["pms_context_fields"] = pms_context_fields
    # AutomationWorkflow derives these from the stored JSON; a MagicMock cannot,
    # and the shared lookup reads them before any trigger-specific matching. The
    # stored JSON here is deliberately the pre-rearchitecture shape — that is what
    # already-published definitions still hold until the rewrite script runs, and
    # the model reports their type verbatim rather than the up-converted one. Note
    # that `_scan_recall_async` compares `trigger_type` to "recall_scan" literally
    # instead of going through `workflow_starts_from`, so a recall campaign
    # authored with the new `schedule` trigger is not picked up by the sweep.
    wf.trigger_type = trigger["type"]
    wf.trigger_types = [trigger["type"]]
    wf.subscribed_event_keys = list(TRIGGER_EVENT_KEYS.get(trigger["type"], ()))
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


def test_workflow_matches_all_appointment_types_when_unfiltered():
    wf = _make_workflow(appointment_type_ids=None)
    assert workflow_matches_appointment(wf, appointment_type_id="gt-9") is True


def test_workflow_matches_selected_appointment_type_id():
    wf = _make_workflow(appointment_type_ids=["gt-9"])
    assert workflow_matches_appointment(wf, appointment_type_id="gt-9") is True
    assert workflow_matches_appointment(wf, appointment_type_id="gt-10") is True


def test_workflow_ignores_legacy_appointment_type_name_filter():
    wf = _make_workflow(appointment_type_ids=["Implant Surgery"])
    assert (
        workflow_matches_appointment(
            wf,
            appointment_type_id=None,
            appointment_type_name="Cleaning",
        )
        is True
    )


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

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=mock_session,
        ),
        patch("src.app.tasks.automation_workflow.AppointmentTriggerService") as MockSvc,
    ):
        instance = AsyncMock()
        instance.find_active_appointment_workflows = AsyncMock(return_value=[])
        instance.get_appointment_context = AsyncMock(return_value={})
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


@pytest.mark.asyncio
async def test_nexhealth_event_facts_win_over_stale_projection_for_filtering():
    appointment_at = datetime.now(tz=timezone.utc) + timedelta(hours=48)
    wf = _make_workflow(
        offset_hours=-24,
        trigger_filter={
            "kind": "group",
            "op": "and",
            "children": [
                {
                    "kind": "rule",
                    "field": "appointment_status",
                    "op": "in_case_insensitive",
                    "value": ["booked"],
                },
                {
                    "kind": "rule",
                    "field": "appointment_reason",
                    "op": "in_case_insensitive",
                    "value": ["Surgery"],
                },
                {
                    "kind": "rule",
                    "field": "nexhealth_payload.appointment.cancelled",
                    "op": "eq",
                    "value": False,
                },
                {
                    "kind": "rule",
                    "field": "nexhealth_payload.appointment.appointment_type_id",
                    "op": "in_case_insensitive",
                    "value": ["4", "nh-4"],
                },
            ],
        },
    )
    mock_session = _make_session(workflows=[wf])

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=mock_session,
        ),
        patch(
            "src.app.tasks.automation_workflow.AppointmentTriggerService"
        ) as mock_svc,
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
        patch(
            "src.app.tasks.automation_workflow._pre_enroll_scheduled_workflow_run",
            new=AsyncMock(return_value=("run-1", True)),
        ) as pre_enroll,
    ):
        instance = AsyncMock()
        instance.find_active_appointment_workflows = AsyncMock(return_value=[wf])
        instance.get_appointment_context = AsyncMock(
            return_value={
                "pms_source": "nexhealth",
                "appointment_status": "scheduled",
                "appointment_reason": None,
                "appointment_type_id": "4",
                "location_id": "loc-1",
            }
        )
        mock_svc.return_value = instance
        mock_task.apply_async = MagicMock()

        result = await _trigger_appointment_async(
            institution_id="inst-1",
            appointment_id="1683218907",
            appointment_at_iso=appointment_at.isoformat(),
            contact_id="contact-1",
            location_id="loc-1",
            trigger_metadata={
                "event": "appointment_insertion",
                "pms_source": "nexhealth",
                "appointment_status": "booked",
                "appointment_reason": "Surgery",
                "appointment_type_name": "Surgery",
                "nexhealth_payload": {
                    "event": "appointment_insertion",
                    "appointment": {
                        "appointment_type_id": "4",
                        "appointment_type_name": "Surgery",
                        "cancelled": False,
                    },
                },
            },
        )

    assert result["scheduled"] == 1
    pre_enroll.assert_awaited_once()
    pre_enroll_kwargs = pre_enroll.await_args.kwargs
    assert pre_enroll_kwargs["trigger_metadata"]["appointment_status"] == "booked"
    assert pre_enroll_kwargs["trigger_metadata"]["appointment_reason"] == "Surgery"
    assert (
        pre_enroll_kwargs["trigger_metadata"]["nexhealth_payload"]["appointment"][
            "appointment_type_id"
        ]
        == "4"
    )
    metadata = mock_task.apply_async.call_args.kwargs["kwargs"]["trigger_metadata"]
    assert metadata["appointment_status"] == "booked"
    assert metadata["appointment_reason"] == "Surgery"
    assert metadata["nexhealth_payload"]["appointment"]["appointment_type_id"] == "4"


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


def test_recall_scan_alert_fires_only_for_eligible_but_unenrolled():
    # Nothing read → not an issue.
    assert _recall_scan_should_alert({"recalls_read": 0, "enrolled": 0}) is False
    # Everything enrolled → fine.
    assert (
        _recall_scan_should_alert({"recalls_read": 5, "enrolled": 5}) is False
    )
    # Read some, enrolled none, and all were filtered for a known reason → no-op.
    assert (
        _recall_scan_should_alert(
            {
                "recalls_read": 5,
                "enrolled": 0,
                "recalls_skipped_recall_type": 5,
                "recalls_skipped_due_window": 0,
            }
        )
        is False
    )
    # Read some, enrolled none, but some passed the filters → silent-failure shape.
    assert (
        _recall_scan_should_alert(
            {
                "recalls_read": 5,
                "enrolled": 0,
                "recalls_skipped_recall_type": 1,
                "recalls_skipped_due_window": 0,
            }
        )
        is True
    )


def test_recall_type_allowlist_matches_raw_or_prefixed_ids():
    # Recall rows carry the raw PMS id; the catalog/UI shows it prefixed.
    recall = {"recall_type": {"id": 2419029}}
    assert _recall_type_allowed(recall, ["nh-2419029"]) is True
    assert _recall_type_allowed(recall, ["2419029"]) is True
    assert _recall_type_allowed(recall, ["nh-999"]) is False
    assert _recall_type_allowed(recall, []) is True  # no allowlist = all types


def test_recall_is_due():
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert _recall_is_due({"due_date": "2026-06-01"}, now=now) is True  # overdue
    assert _recall_is_due({"due_date": "2026-09-01"}, now=now) is False  # future
    assert (
        _recall_is_due({"recall_due_date": "2026-09-01"}, now=now) is False
    )  # synchronizer contract name
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
# _tick_workflow_schedules_async — recall schedules enroll, not crash
# ---------------------------------------------------------------------------


def _recall_schedule_workflow():
    workflow = MagicMock()
    workflow.id = "wf-1"
    workflow.institution_id = "inst-1"
    workflow.location_id = None
    workflow.definition = {
        "trigger": {
            "type": "schedule",
            "cron": "0 9 * * *",
            "source": {
                "kind": "pms_recall",
                "recall_interval_months": 6,
                "reenrollment_cooldown_days": 90,
            },
            "max_enrollments_per_run": 7,
        },
        "entry_node_id": "exit-1",
        "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
    }
    return workflow


def _schedule_row(workflow_id="wf-1", location_id=None, version_id="ver-9"):
    return SimpleNamespace(
        workflow_id=workflow_id,
        institution_id="inst-1",
        location_id=location_id,
        workflow_version_id=version_id,
    )


@pytest.mark.asyncio
async def test_tick_schedule_hands_scanner_canonical_recall_entries():
    import src.app.tasks.automation_workflow as aw

    workflow = _recall_schedule_workflow()
    schedule_session = AsyncMock()
    workflow_session = AsyncMock()
    workflow_session.get = AsyncMock(return_value=workflow)
    enroll = AsyncMock(return_value={"enrolled": 5})

    with (
        patch.object(
            aw, "_superadmin_system_session", return_value=_cm(schedule_session)
        ),
        patch(
            "src.app.services.automation.schedule_service.WorkflowScheduleService.claim_due",
            AsyncMock(return_value=[_schedule_row()]),
        ),
        patch.object(aw, "get_system_db_session", return_value=_cm(workflow_session)),
        patch.object(aw, "_enroll_recalls_for_institution", enroll),
    ):
        result = await aw._tick_workflow_schedules_async()

    enroll.assert_awaited_once()
    institution_id, entries = enroll.await_args.args
    assert institution_id == "inst-1"
    assert len(entries) == 1
    entry = entries[0]
    # The shape _enroll_recalls_for_institution actually reads. The old tick
    # wrote `workflow_version_id`/`definition`, so every scheduled recall hit
    # KeyError('workflow') and enrolled nothing.
    assert entry["workflow"] is workflow
    assert entry["version_id"] == "ver-9"
    assert entry["workflow_id"] == "wf-1"
    assert entry["location_id"] is None
    assert entry["max_enrollments"] == 7
    # `enrolled` is the scanner counter; the tick used to read a key that
    # never existed and always reported zero.
    assert result["recall"] == 5


@pytest.mark.asyncio
async def test_tick_schedule_collapses_duplicate_recall_rows():
    import src.app.tasks.automation_workflow as aw

    workflow = _recall_schedule_workflow()
    workflow_session = AsyncMock()
    workflow_session.get = AsyncMock(return_value=workflow)
    enroll = AsyncMock(return_value={"enrolled": 0})

    with (
        patch.object(aw, "_superadmin_system_session", return_value=_cm(AsyncMock())),
        patch(
            "src.app.services.automation.schedule_service.WorkflowScheduleService.claim_due",
            # An institution-wide workflow has one schedule row per location.
            AsyncMock(return_value=[_schedule_row(), _schedule_row()]),
        ),
        patch.object(aw, "get_system_db_session", return_value=_cm(workflow_session)),
        patch.object(aw, "_enroll_recalls_for_institution", enroll),
    ):
        await aw._tick_workflow_schedules_async()

    _, entries = enroll.await_args.args
    assert len(entries) == 1


# ---------------------------------------------------------------------------
# _scan_recall_async — enrolls due patients from the NexHealth recall list
# ---------------------------------------------------------------------------


def _cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _scalar_result(value=None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_scan_recall_async_enrolls_due_patients():
    # One active recall workflow for one institution.
    wf = _make_workflow(trigger_type="recall_scan", version_id="ver-1")
    scan_session = _make_session(workflows=[wf])

    # Institution-scoped session: get(Institution) truthy, one configured location,
    # then contact/future-appointment/cooldown checks per due patient.
    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=MagicMock())  # Institution present
    location = MagicMock()
    location.id = "loc-1"
    location.nexhealth_subdomain = "sub"
    location.nexhealth_location_id = "nexloc-1"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    contact_result = _scalar_result(None)
    future_result = _scalar_result(None)
    cooldown_result = _scalar_result(None)
    inst_session.execute = AsyncMock(
        side_effect=[
            loc_result,
            contact_result,
            future_result,
            cooldown_result,
            contact_result,
            future_result,
            cooldown_result,
            contact_result,
            future_result,
            cooldown_result,
        ]
    )

    recalls = [
        {"patient_id": "p1", "due_date": "2020-01-01"},
        {"patient_id": "p2", "due_date": "2020-01-01"},
        {"patient_id": "p3", "due_date": "2020-01-01"},
    ]
    adapter = AsyncMock()
    adapter.list_patient_recalls = AsyncMock(return_value=recalls)
    adapter.close = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    # 3 due patients × 1 workflow = 3 enrollment tasks queued.
    assert result["enrolled"] == 3
    assert result["institutions"] == 1
    assert mock_task.apply_async.call_count == 3
    # The scanner must request the whole recall queue, not the adapter default.
    assert adapter.list_patient_recalls.await_args.kwargs.get("max_items") is None

    # Idempotency keys follow recall:{version}:{patient}:{period}.
    keys = {
        c.kwargs["kwargs"]["idempotency_key"]
        for c in mock_task.apply_async.call_args_list
    }
    assert all(k.startswith("recall:ver-1:") for k in keys)
    assert len(keys) == 3  # distinct per patient
    # Recall runs carry the recall trigger ref type.
    assert all(
        c.kwargs["kwargs"]["trigger_ref_type"] == "recall"
        for c in mock_task.apply_async.call_args_list
    )
    assert all(
        c.kwargs["kwargs"]["trigger_metadata"]["recall_reenrollment_cooldown_days"]
        == 90
        for c in mock_task.apply_async.call_args_list
    )


def _recall_inst_session(patient_count):
    """Institution session whose execute() feeds one recall-scan per patient."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=MagicMock())
    location = MagicMock()
    location.id = "loc-1"
    location.nexhealth_subdomain = "sub"
    location.nexhealth_location_id = "nexloc-1"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    results = [loc_result]
    for _ in range(patient_count):
        # contact lookup, future-appointment probe, existing-enrollment probe
        results.extend([_scalar_result(None), _scalar_result(None), _scalar_result(None)])
    session.execute = AsyncMock(side_effect=results)
    return session, location


async def _run_scan_with_recalls(scan_session, inst_session, recalls):
    adapter = AsyncMock()
    adapter.list_patient_recalls = AsyncMock(return_value=recalls)
    adapter.close = AsyncMock()
    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()
    return result, mock_task


@pytest.mark.asyncio
async def test_scan_recall_honors_approved_recall_type_allowlist():
    wf = _make_workflow(
        recall_source={
            "approved_recall_type_ids": ["2288000"],
            "due_within_days": 365,
        },
        version_id="ver-1",
    )
    scan_session = _make_session(workflows=[wf])
    # The contact/future-appointment probes run per grouped patient before the
    # per-workflow allowlist filter, so budget one slot per patient.
    inst_session, _ = _recall_inst_session(2)
    recalls = [
        {
            "id": 1,
            "patient_id": "p1",
            "date_due": "2020-01-01",
            "recall_type": {"id": 2288000},
        },
        {
            "id": 2,
            "patient_id": "p2",
            "date_due": "2020-01-01",
            "recall_type": {"id": 2288002},  # BW's — not approved
        },
    ]

    result, mock_task = await _run_scan_with_recalls(
        scan_session, inst_session, recalls
    )

    assert result["enrolled"] == 1
    assert (
        mock_task.apply_async.call_args.kwargs["kwargs"]["trigger_ref_id"] == "p1"
    )


@pytest.mark.asyncio
async def test_scan_recall_window_covers_next_30_days_and_overdue():
    wf = _make_workflow(
        recall_source={"due_within_days": 30, "include_overdue": True},
        version_id="ver-1",
    )
    scan_session = _make_session(workflows=[wf])
    inst_session, _ = _recall_inst_session(3)
    today = datetime.now(timezone.utc).date()

    def due(days):
        return (today + timedelta(days=days)).isoformat()

    recalls = [
        {"id": 1, "patient_id": "soon", "date_due": due(10), "recall_type": {"id": 2288000}},
        {"id": 2, "patient_id": "far", "date_due": due(60), "recall_type": {"id": 2288000}},
        {"id": 3, "patient_id": "overdue", "date_due": due(-5), "recall_type": {"id": 2288000}},
    ]

    result, mock_task = await _run_scan_with_recalls(
        scan_session, inst_session, recalls
    )

    assert result["enrolled"] == 2
    refs = {
        c.kwargs["kwargs"]["trigger_ref_id"]
        for c in mock_task.apply_async.call_args_list
    }
    assert refs == {"soon", "overdue"}


@pytest.mark.asyncio
async def test_scan_recall_coming_due_only_skips_overdue_and_undated():
    wf = _make_workflow(
        recall_source={"due_within_days": 30, "include_overdue": False},
        version_id="ver-1",
    )
    scan_session = _make_session(workflows=[wf])
    inst_session, _ = _recall_inst_session(4)
    today = datetime.now(timezone.utc).date()

    def due(days):
        return (today + timedelta(days=days)).isoformat()

    recalls = [
        {"id": 1, "patient_id": "soon", "date_due": due(10), "recall_type": {"id": 2288000}},
        {"id": 2, "patient_id": "far", "date_due": due(60), "recall_type": {"id": 2288000}},
        {"id": 3, "patient_id": "overdue", "date_due": due(-5), "recall_type": {"id": 2288000}},
        {"id": 4, "patient_id": "undated", "recall_type": {"id": 2288000}},
    ]

    result, mock_task = await _run_scan_with_recalls(
        scan_session, inst_session, recalls
    )

    assert result["enrolled"] == 1
    assert result["recalls_skipped_due_window"] == 3
    assert (
        mock_task.apply_async.call_args.kwargs["kwargs"]["trigger_ref_id"] == "soon"
    )


@pytest.mark.asyncio
async def test_scan_recall_groups_multiple_recalls_into_one_enrollment():
    wf = _make_workflow(
        recall_source={
            "approved_recall_type_ids": ["2288000", "2288006"],
            "due_within_days": 365,
        },
        version_id="ver-1",
    )
    scan_session = _make_session(workflows=[wf])
    inst_session, _ = _recall_inst_session(1)
    recalls = [
        {"id": 1, "patient_id": "p1", "date_due": "2021-01-01", "recall_type": {"id": 2288006}},
        {"id": 2, "patient_id": "p1", "date_due": "2020-01-01", "recall_type": {"id": 2288000}},
    ]

    result, mock_task = await _run_scan_with_recalls(
        scan_session, inst_session, recalls
    )

    assert result["enrolled"] == 1
    kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
    assert kwargs["trigger_ref_id"] == "p1"
    # Primary is the earliest due recall, so the booking type mapping is stable.
    assert kwargs["trigger_metadata"]["recall_due_date"] == "2020-01-01"


@pytest.mark.asyncio
async def test_scan_recall_applies_declared_patient_communication_context_filter():
    wf = _make_workflow(
        trigger_type="recall_scan",
        version_id="ver-1",
        trigger_filter={
            "kind": "group",
            "op": "and",
            "children": [
                {
                    "kind": "rule",
                    "field": "recall_type_name",
                    "op": "eq",
                    "value": "Hygiene",
                },
                {
                    "kind": "rule",
                    "field": "has_active_treatment_plan",
                    "op": "eq",
                    "value": False,
                },
            ],
        },
        pms_context_fields=[
            "recall_type_name",
            "has_active_treatment_plan",
            "treatment_plan_statuses",
        ],
    )
    scan_session = _make_session(workflows=[wf])

    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=MagicMock())
    location = MagicMock()
    location.id = "loc-1"
    location.timezone = "America/New_York"
    location.nexhealth_subdomain = "sub"
    location.nexhealth_location_id = "nexloc-1"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    contact_result = _scalar_result(None)
    future_result = _scalar_result(None)
    cooldown_result = _scalar_result(None)
    inst_session.execute = AsyncMock(
        side_effect=[
            loc_result,
            contact_result,
            future_result,
            cooldown_result,
            contact_result,
            future_result,
        ]
    )

    adapter = AsyncMock()
    adapter.source = "nexhealth"
    adapter.list_patient_recalls = AsyncMock(
        return_value=[
            {"patient_id": "p1", "recall_id": 7, "date_due": "2020-01-01"},
            {"patient_id": "p2", "recall_id": 7, "date_due": "2020-01-01"},
        ]
    )
    adapter.list_recall_types = AsyncMock(
        return_value=[
            UniversalRecallType(
                id="nh-7",
                source="nexhealth",
                name="Hygiene",
                interval_months=6,
            )
        ]
    )
    adapter.list_treatment_plans = AsyncMock(
        side_effect=[
            [],
            [
                UniversalTreatmentPlan(
                    id="nh-8",
                    source="nexhealth",
                    patient_id="nh-p2",
                    status="accepted",
                )
            ],
        ]
    )
    adapter.close = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    assert result["enrolled"] == 1
    adapter.list_treatment_plans.assert_any_await(patient_id="p1", max_items=100)
    adapter.list_treatment_plans.assert_any_await(patient_id="p2", max_items=100)
    kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
    period = datetime.now(tz=timezone.utc).strftime("%Y-%m")
    assert kwargs["trigger_ref_id"] == "p1"
    # Subset, not equality: the canonical projection now adds a `recall.*` and
    # `trigger.*` section on top. The legacy flat keys are what published
    # definitions branch on, so those are what must not move.
    metadata = kwargs["trigger_metadata"]
    assert (
        metadata.items()
        >= {
            "nexhealth_patient_id": "p1",
            "recall_due_date": "2020-01-01",
            "recall_period": period,
            "recall_type_name": "Hygiene",
            "treatment_plan_statuses": [],
            "has_active_treatment_plan": False,
            "recall_reenrollment_cooldown_days": 90,
        }.items()
    )
    # …and the canonical view of the same facts is present for new campaigns.
    assert metadata["recall"]["due_at"] == "2020-01-01"
    assert metadata["recall"]["type"] == "Hygiene"
    assert metadata["trigger"]["key"] == "patient.recall_due"
    assert "treatment_plans" not in kwargs["trigger_metadata"]


@pytest.mark.asyncio
async def test_scan_recall_skips_patients_with_future_appointments():
    wf = _make_workflow(trigger_type="recall_scan", version_id="ver-1")
    scan_session = _make_session(workflows=[wf])

    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=MagicMock())
    location = MagicMock()
    location.id = "loc-1"
    location.nexhealth_subdomain = "sub"
    location.nexhealth_location_id = "nexloc-1"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    inst_session.execute = AsyncMock(
        side_effect=[
            loc_result,
            _scalar_result(None),
            _scalar_result("future-appt-1"),
        ]
    )

    adapter = AsyncMock()
    adapter.source = "nexhealth"
    adapter.list_patient_recalls = AsyncMock(
        return_value=[{"patient_id": "p1", "recall_id": 7, "date_due": "2020-01-01"}]
    )
    adapter.list_recall_types = AsyncMock(return_value=[])
    adapter.close = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    assert result["enrolled"] == 0
    mock_task.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_scan_recall_skips_patients_inside_reenrollment_cooldown():
    wf = _make_workflow(trigger_type="recall_scan", version_id="ver-1")
    wf.definition["trigger"]["recall_reenrollment_cooldown_days"] = 90
    scan_session = _make_session(workflows=[wf])

    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=MagicMock())
    location = MagicMock()
    location.id = "loc-1"
    location.nexhealth_subdomain = "sub"
    location.nexhealth_location_id = "nexloc-1"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    inst_session.execute = AsyncMock(
        side_effect=[
            loc_result,
            _scalar_result(None),
            _scalar_result(None),
            _scalar_result("recent-run-1"),
        ]
    )

    adapter = AsyncMock()
    adapter.source = "nexhealth"
    adapter.list_patient_recalls = AsyncMock(
        return_value=[{"patient_id": "p1", "recall_id": 7, "date_due": "2020-01-01"}]
    )
    adapter.list_recall_types = AsyncMock(return_value=[])
    adapter.close = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    assert result["enrolled"] == 0
    mock_task.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_scan_recall_skips_when_declared_treatment_context_cannot_be_read():
    wf = _make_workflow(
        trigger_type="recall_scan",
        version_id="ver-1",
        trigger_filter={
            "kind": "rule",
            "field": "has_active_treatment_plan",
            "op": "eq",
            "value": False,
        },
        pms_context_fields=["has_active_treatment_plan"],
    )
    scan_session = _make_session(workflows=[wf])

    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=MagicMock())
    location = MagicMock()
    location.id = "loc-1"
    location.nexhealth_subdomain = "sub"
    location.nexhealth_location_id = "nexloc-1"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    inst_session.execute = AsyncMock(
        side_effect=[
            loc_result,
            _scalar_result(None),
            _scalar_result(None),
        ]
    )

    adapter = AsyncMock()
    adapter.source = "nexhealth"
    adapter.list_patient_recalls = AsyncMock(
        return_value=[{"patient_id": "p1", "recall_id": 7, "date_due": "2020-01-01"}]
    )
    adapter.list_recall_types = AsyncMock(return_value=[])
    adapter.list_treatment_plans = AsyncMock(side_effect=RuntimeError("PMS down"))
    adapter.close = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    assert result["enrolled"] == 0
    mock_task.apply_async.assert_not_called()
    adapter.list_treatment_plans.assert_awaited_once_with(
        patient_id="p1", max_items=100
    )


@pytest.mark.asyncio
async def test_scan_recall_enrolls_gotracker_with_completed_history_and_inline_context():
    wf = _make_workflow(
        trigger_type="recall_scan",
        version_id="ver-1",
        trigger_filter={
            "kind": "group",
            "op": "and",
            "children": [
                {
                    "kind": "rule",
                    "field": "recall_type_name",
                    "op": "eq",
                    "value": "Hygiene",
                },
                {
                    "kind": "rule",
                    "field": "has_active_treatment_plan",
                    "op": "eq",
                    "value": False,
                },
            ],
        },
        pms_context_fields=[
            "recall_type_name",
            "has_active_treatment_plan",
            "treatment_plan_statuses",
        ],
    )
    scan_session = _make_session(workflows=[wf])

    institution = SimpleNamespace(id="inst-1", pms_type="gotracker")
    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=institution)
    location = MagicMock()
    location.id = "loc-1"
    location.timezone = "America/New_York"
    location.gotracker_product_key_encrypted = "encrypted-key"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    inst_session.execute = AsyncMock(
        side_effect=[
            loc_result,
            _scalar_result(None),  # contact lookup for p1
            _scalar_result(None),  # no future appointment for p1
            _scalar_result(None),  # no recent visit for p1
            _scalar_result(None),  # no cooldown for p1
            _scalar_result(None),  # contact lookup for p2
            _scalar_result(None),  # no future appointment for p2
            _scalar_result(None),  # no recent visit for p2
        ]
    )

    adapter = SimpleNamespace(
        source="gotracker",
        get_recall_history_sync_status=AsyncMock(
            return_value={"appointment_history": {"complete": True}}
        ),
        list_patient_recalls=AsyncMock(
            return_value=[
                {
                    "patient_id": "p1",
                    "recall_type_name": "Hygiene",
                    "due_date": "2020-01-01",
                    "has_active_treatment_plan": False,
                },
                {
                    "patient_id": "p2",
                    "recall_type_name": "Hygiene",
                    "due_date": "2020-01-01",
                    "has_active_treatment_plan": True,
                },
            ]
        ),
        list_recall_types=AsyncMock(return_value=[]),
        close=AsyncMock(),
    )

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.tasks.automation_workflow._create_recall_adapter",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    assert result["enrolled"] == 1
    assert result["locations_skipped_history_incomplete"] == 0
    assert result["patients_skipped_recent_visit"] == 0
    kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
    period = datetime.now(tz=timezone.utc).strftime("%Y-%m")
    assert kwargs["trigger_ref_id"] == "gt-p1"
    # Subset, not equality: the canonical projection now adds a `recall.*` and
    # `trigger.*` section on top. The legacy flat keys are what published
    # definitions branch on, so those are what must not move.
    metadata = kwargs["trigger_metadata"]
    assert (
        metadata.items()
        >= {
            "nexhealth_patient_id": "gt-p1",
            "recall_due_date": "2020-01-01",
            "recall_period": period,
            "recall_type_name": "Hygiene",
            "treatment_plan_statuses": [],
            "has_active_treatment_plan": False,
            "recall_reenrollment_cooldown_days": 90,
        }.items()
    )
    # …and the canonical view of the same facts is present for new campaigns.
    assert metadata["recall"]["due_at"] == "2020-01-01"
    assert metadata["recall"]["type"] == "Hygiene"
    assert metadata["trigger"]["key"] == "patient.recall_due"
    assert not hasattr(adapter, "list_treatment_plans")


@pytest.mark.asyncio
async def test_scan_recall_refuses_gotracker_when_history_sync_is_incomplete():
    wf = _make_workflow(trigger_type="recall_scan", version_id="ver-1")
    scan_session = _make_session(workflows=[wf])

    institution = SimpleNamespace(id="inst-1", pms_type="gotracker")
    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=institution)
    location = MagicMock()
    location.id = "loc-1"
    location.gotracker_product_key_encrypted = "encrypted-key"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    inst_session.execute = AsyncMock(side_effect=[loc_result])

    adapter = SimpleNamespace(
        source="gotracker",
        get_recall_history_sync_status=AsyncMock(
            return_value={
                "appointment_history": {"status": "running", "progress_percent": 42}
            }
        ),
        list_patient_recalls=AsyncMock(return_value=[]),
        close=AsyncMock(),
    )

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.tasks.automation_workflow._create_recall_adapter",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    assert result["enrolled"] == 0
    assert result["locations_skipped_history_incomplete"] == 1
    adapter.list_patient_recalls.assert_not_awaited()
    mock_task.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_scan_recall_skips_gotracker_patient_with_recent_visit():
    wf = _make_workflow(trigger_type="recall_scan", version_id="ver-1")
    scan_session = _make_session(workflows=[wf])

    institution = SimpleNamespace(id="inst-1", pms_type="gotracker")
    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=institution)
    location = MagicMock()
    location.id = "loc-1"
    location.gotracker_product_key_encrypted = "encrypted-key"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    inst_session.execute = AsyncMock(
        side_effect=[
            loc_result,
            _scalar_result(None),
            _scalar_result(None),
            _scalar_result("recent-visit-1"),
        ]
    )

    adapter = SimpleNamespace(
        source="gotracker",
        get_recall_history_sync_status=AsyncMock(
            return_value={"appointment_history": {"complete": True}}
        ),
        list_patient_recalls=AsyncMock(
            return_value=[
                {
                    "patient_id": "p1",
                    "recall_type_name": "Hygiene",
                    "due_date": "2020-01-01",
                }
            ]
        ),
        list_recall_types=AsyncMock(return_value=[]),
        close=AsyncMock(),
    )

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.tasks.automation_workflow._create_recall_adapter",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    assert result["enrolled"] == 0
    assert result["patients_skipped_recent_visit"] == 1
    mock_task.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_scan_recall_skips_gotracker_when_treatment_context_is_missing():
    wf = _make_workflow(
        trigger_type="recall_scan",
        version_id="ver-1",
        trigger_filter={
            "kind": "rule",
            "field": "has_active_treatment_plan",
            "op": "eq",
            "value": False,
        },
        pms_context_fields=["has_active_treatment_plan"],
    )
    scan_session = _make_session(workflows=[wf])

    institution = SimpleNamespace(id="inst-1", pms_type="gotracker")
    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=institution)
    location = MagicMock()
    location.id = "loc-1"
    location.gotracker_product_key_encrypted = "encrypted-key"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    inst_session.execute = AsyncMock(
        side_effect=[
            loc_result,
            _scalar_result(None),
            _scalar_result(None),
            _scalar_result(None),
        ]
    )

    adapter = SimpleNamespace(
        source="gotracker",
        get_recall_history_sync_status=AsyncMock(
            return_value={"appointment_history": {"complete": True}}
        ),
        list_patient_recalls=AsyncMock(
            return_value=[
                {
                    "patient_id": "p1",
                    "recall_type_name": "Hygiene",
                    "due_date": "2020-01-01",
                }
            ]
        ),
        list_recall_types=AsyncMock(return_value=[]),
        close=AsyncMock(),
    )

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.tasks.automation_workflow._create_recall_adapter",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    assert result["enrolled"] == 0
    assert result["patients_skipped_missing_treatment_context"] == 1
    mock_task.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_scan_recall_skips_workflows_bound_to_another_location():
    """A recall workflow bound to location B must not enroll location A's recalls."""
    wf = _make_workflow(
        trigger_type="recall_scan", version_id="ver-1", location_id="loc-other"
    )
    scan_session = _make_session(workflows=[wf])

    inst_session = AsyncMock()
    inst_session.get = AsyncMock(return_value=MagicMock())
    location = MagicMock()
    location.id = "loc-1"
    location.nexhealth_subdomain = "sub"
    location.nexhealth_location_id = "nexloc-1"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    contact_result = MagicMock()
    contact_result.scalar_one_or_none.return_value = None
    inst_session.execute = AsyncMock(side_effect=[loc_result, contact_result])

    adapter = AsyncMock()
    adapter.list_patient_recalls = AsyncMock(
        return_value=[{"patient_id": "p1", "due_date": "2020-01-01"}]
    )
    adapter.close = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=[_cm(scan_session), _cm(inst_session)],
        ),
        patch(
            "src.app.pms.nexhealth.adapter.NexHealthAdapter.create",
            AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
        mock_task.apply_async = MagicMock()
        result = await _scan_recall_async()

    assert result["enrolled"] == 0
    assert mock_task.apply_async.call_count == 0


# ---------------------------------------------------------------------------
# _enroll_and_start_async — duplicate idempotency key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_and_start_duplicate_key_skips():
    existing_run = MagicMock()
    existing_run.id = "run-existing"
    existing_run.status = "waiting"

    mock_enroll_svc = AsyncMock()
    mock_enroll_svc.enroll = AsyncMock(return_value=(existing_run, False))

    mock_session = _make_session()

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=mock_session,
        ),
        patch(
            "src.app.tasks.automation_workflow.AutomationWorkflowEnrollmentService",
            return_value=mock_enroll_svc,
        ),
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


@pytest.mark.asyncio
async def test_enroll_and_start_starts_pre_enrolled_pending_run():
    existing_run = SimpleNamespace(
        id="run-existing",
        status=AutomationRunStatus.PENDING.value,
        current_step_id=None,
        trigger_metadata={},
    )
    version = SimpleNamespace(
        id="ver-1",
        definition={
            "trigger": {"type": "manual"},
            "entry_node_id": "exit-1",
            "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
        },
    )

    mock_enroll_svc = AsyncMock()
    mock_enroll_svc.enroll = AsyncMock(return_value=(existing_run, False))

    mock_session = _make_session()
    mock_session.get = AsyncMock(return_value=version)

    dispatcher = SimpleNamespace(
        runtime=SimpleNamespace(start_run=AsyncMock()),
        advance=AsyncMock(
            return_value=SimpleNamespace(
                status="completed",
                outcome="done",
                steps_advanced=1,
                patient_status_event_ids=[],
            )
        ),
    )

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=mock_session,
        ),
        patch(
            "src.app.tasks.automation_workflow.AutomationWorkflowEnrollmentService",
            return_value=mock_enroll_svc,
        ),
        patch(
            "src.app.tasks.automation_workflow.build_dispatcher",
            new=AsyncMock(return_value=(dispatcher, "UTC")),
        ),
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

    dispatcher.runtime.start_run.assert_awaited_once_with(existing_run)
    dispatcher.advance.assert_awaited_once()
    assert result["created"] is False
    assert result["started"] is True
    assert result["dispatch_status"] == "completed"


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

    data = BulkEnrollRequest(
        items=[
            BulkEnrollItem(contact_id="c-1", idempotency_key="k-1"),
            BulkEnrollItem(contact_id="c-2", idempotency_key="k-2"),
        ]
    )

    with (
        patch(
            "src.app.api.routes.automation_workflows.get_db_session",
            return_value=mock_session,
        ),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
        patch(
            "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
        ) as mock_task,
    ):
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

    data = BulkEnrollRequest(
        items=[BulkEnrollItem(contact_id="c-1", idempotency_key="k-1")]
    )

    with (
        patch(
            "src.app.api.routes.automation_workflows.get_db_session",
            return_value=mock_session,
        ),
        patch(
            "src.app.api.routes.automation_workflows.AutomationWorkflowDefinitionService",
            return_value=mock_svc,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(bulk_enroll("wf-1", data, user))

    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# Live patient refresh at recall enrollment
# ---------------------------------------------------------------------------


def _single_recall_scan():
    wf = _make_workflow(
        recall_source={"due_within_days": 365}, version_id="ver-1"
    )
    recalls = [
        {"id": 1, "patient_id": "p1", "date_due": "2020-01-01", "recall_type": {"id": 2288000}}
    ]
    return _make_session(workflows=[wf]), recalls


@pytest.mark.asyncio
async def test_scan_recall_enrolls_the_live_contact_with_last_visit(_live_recall_refresh):
    _live_recall_refresh.return_value = _LiveRecallPatient(
        contact_id="contact-live", inactive=False, last_visit_date="2026-04-01"
    )
    scan_session, recalls = _single_recall_scan()
    inst_session, _ = _recall_inst_session(1)

    result, mock_task = await _run_scan_with_recalls(scan_session, inst_session, recalls)

    assert result["enrolled"] == 1
    assert _live_recall_refresh.await_args.kwargs["patient_id"] == "p1"
    kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
    assert kwargs["contact_id"] == "contact-live"
    assert kwargs["trigger_metadata"]["last_visit_date"] == "2026-04-01"


@pytest.mark.asyncio
async def test_scan_recall_never_calls_a_patient_inactive_in_the_pms(_live_recall_refresh):
    _live_recall_refresh.return_value = _LiveRecallPatient(
        contact_id="contact-live", inactive=True, last_visit_date=None
    )
    scan_session, recalls = _single_recall_scan()
    inst_session, _ = _recall_inst_session(1)

    result, mock_task = await _run_scan_with_recalls(scan_session, inst_session, recalls)

    assert result["enrolled"] == 0
    assert result["patients_skipped_inactive"] == 1
    mock_task.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_scan_recall_skips_a_patient_nexhealth_shows_as_already_booked(
    _live_recall_refresh,
):
    _live_recall_refresh.return_value = _LiveRecallPatient(
        contact_id=None, inactive=False, last_visit_date=None, has_future_appointment=True
    )
    scan_session, recalls = _single_recall_scan()
    inst_session, _ = _recall_inst_session(1)

    result, mock_task = await _run_scan_with_recalls(scan_session, inst_session, recalls)

    assert result["enrolled"] == 0
    assert result["patients_skipped_future_appointment_live"] == 1
    mock_task.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_scan_recall_skips_unreadable_patient_without_stored_contact(_live_recall_refresh):
    _live_recall_refresh.return_value = None
    scan_session, recalls = _single_recall_scan()
    inst_session, _ = _recall_inst_session(1)

    result, mock_task = await _run_scan_with_recalls(scan_session, inst_session, recalls)

    assert result["enrolled"] == 0
    assert result["patients_skipped_live_fetch_failed"] == 1
    mock_task.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_scan_recall_falls_back_to_stored_contact_when_live_read_fails(
    _live_recall_refresh,
):
    _live_recall_refresh.return_value = None
    scan_session, recalls = _single_recall_scan()
    inst_session, location = _recall_inst_session(0)
    stored = MagicMock()
    stored.id = "contact-stored"
    loc_result = MagicMock()
    loc_result.scalars.return_value.all.return_value = [location]
    inst_session.execute = AsyncMock(
        side_effect=[loc_result, _scalar_result(stored), _scalar_result(None), _scalar_result(None)]
    )

    result, mock_task = await _run_scan_with_recalls(scan_session, inst_session, recalls)

    assert result["enrolled"] == 1
    kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
    assert kwargs["contact_id"] == "contact-stored"


def _savepoint_session():
    session = AsyncMock()
    savepoint = MagicMock()
    savepoint.__aenter__ = AsyncMock(return_value=None)
    savepoint.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=savepoint)
    return session


def _live_location():
    return SimpleNamespace(id="loc-1", nexhealth_location_id="nexloc-1")


@pytest.mark.asyncio
async def test_refresh_recall_contact_live_upserts_and_commits():
    session = _savepoint_session()
    adapter = MagicMock()
    adapter.get_patient_raw = AsyncMock(
        return_value={
            "id": 123,
            "location_ids": [55],
            "inactive": False,
            "last_visited_appointment": {"start_time": "2026-04-01T14:00:00.000Z"},
        }
    )
    upsert = AsyncMock(return_value=SimpleNamespace(contact=SimpleNamespace(id="c-9")))
    with patch(
        "src.app.services.automation.nexhealth_projection_service.NexHealthProjectionService"
    ) as service:
        service.return_value.upsert_patient = upsert
        live = await _refresh_recall_contact_live(
            session, adapter, institution_id="inst-1", location=_live_location(), patient_id="123"
        )

    assert live == _LiveRecallPatient(
        contact_id="c-9", inactive=False, last_visit_date="2026-04-01"
    )
    adapter.get_patient_raw.assert_awaited_once_with(
        "123", include=["last_visited_appointment", "upcoming_appts"]
    )
    assert upsert.await_args.kwargs["local_location_ids"] == ["loc-1"]
    assert upsert.await_args.kwargs["nexhealth_location_ids"] == ["55"]
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_recall_contact_live_returns_none_when_patient_unreadable():
    session = _savepoint_session()
    adapter = MagicMock()
    adapter.get_patient_raw = AsyncMock(return_value=None)

    live = await _refresh_recall_contact_live(
        session, adapter, institution_id="inst-1", location=_live_location(), patient_id="123"
    )

    assert live is None
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_recall_contact_live_returns_none_when_upsert_fails():
    session = _savepoint_session()
    adapter = MagicMock()
    adapter.get_patient_raw = AsyncMock(return_value={"id": 123})
    with patch(
        "src.app.services.automation.nexhealth_projection_service.NexHealthProjectionService"
    ) as service:
        service.return_value.upsert_patient = AsyncMock(side_effect=RuntimeError("db"))
        live = await _refresh_recall_contact_live(
            session, adapter, institution_id="inst-1", location=_live_location(), patient_id="123"
        )

    assert live is None
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_recall_contact_live_reports_upcoming_visit_without_storing():
    session = _savepoint_session()
    adapter = MagicMock()
    adapter.get_patient_raw = AsyncMock(
        return_value={
            "id": 123,
            "inactive": False,
            "upcoming_appts": [{"id": 9, "start_time": "2026-07-20T14:00:00.000Z"}],
        }
    )
    with patch(
        "src.app.services.automation.nexhealth_projection_service.NexHealthProjectionService"
    ) as service:
        live = await _refresh_recall_contact_live(
            session, adapter, institution_id="inst-1", location=_live_location(),
            patient_id="123", now=_NOW,
        )

    assert live.has_future_appointment is True
    assert live.contact_id is None
    service.return_value.upsert_patient.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    ("upcoming", "booked"),
    [
        ([], False),
        (None, False),
        ([{"start_time": "2026-07-01T14:00:00.000Z"}], False),  # already started
        ([{"start_time": "2026-07-10T12:00:00Z"}], True),
        ([{"start_time": "2026-08-01T09:00:00"}], True),  # no offset: read as UTC
        ([{"start_time": "garbled"}], True),  # unreadable: do not risk the call
    ],
)
def test_has_upcoming_appointment(upcoming, booked):
    from src.app.tasks.automation_workflow import _has_upcoming_appointment

    assert _has_upcoming_appointment({"upcoming_appts": upcoming}, _NOW) is booked
