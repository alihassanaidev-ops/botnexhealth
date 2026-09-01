"""Unit tests for automation workflow Celery task helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.config import settings
from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationTimerStatus,
    AutomationWorkflowRun,
    AutomationWorkflowTimer,
    AutomationWorkflowVersion,
)
from src.app.pms.base import SupportsAppointmentConfirmation
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.tasks.automation_workflow import (
    _claim_and_enqueue_async,
    _confirm_appointments_for_runs,
    _dispatch_timer_async,
    _dial_outcome_for_attempt,
    _poll_retell_voice_outcomes_async,
    _retell_call_details_outcome,
    _retell_call_details_ready_for_resume,
    _resume_sms_confirmation_async,
    _resolve_gotracker_writeback_target,
    _trigger_appointment_state_async,
    _trigger_patient_status_async,
    _waiting_step_targets_field,
    _retry_countdown,
)

_NOW = datetime(2026, 7, 2, 14, 0, 0, tzinfo=timezone.utc)


class _FakeConfirmAdapter(SupportsAppointmentConfirmation):
    def __init__(self) -> None:
        self.confirmed: list[str] = []
        self.closed = False

    async def confirm_appointment(self, appointment_id: str):
        self.confirmed.append(appointment_id)
        return SimpleNamespace(success=True, status="confirmed", error=None)

    async def close(self) -> None:
        self.closed = True

_VALID_DEFINITION = {
    "trigger": {"type": "manual"},
    "entry_node_id": "exit-1",
    "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
}

_WAIT_CONFIRM_DEFINITION = {
    "trigger": {"type": "appointment_offset", "offset_hours": -48},
    "entry_node_id": "wait-response",
    "nodes": [
        {
            "type": "wait",
            "id": "wait-response",
            "delay": {"delay_type": "duration", "duration_seconds": 7200},
            "next_node_id": "check-confirmed",
        },
        {
            "type": "condition",
            "id": "check-confirmed",
            "rules": [{"field": "appointment_status", "op": "eq", "value": "confirmed"}],
            "true_next_node_id": "exit-confirmed",
            "false_next_node_id": "exit-no-response",
        },
        {"type": "exit", "id": "exit-confirmed", "outcome": "confirmed"},
        {"type": "exit", "id": "exit-no-response", "outcome": "no_response"},
    ],
}

_SMS_MAPPING_DEFINITION = {
    "trigger": {"type": "manual"},
    "entry_node_id": "sms-1",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-1",
            "body_template": "Reply DONE when forms are complete.",
            "next_node_id": "wait-response",
            "expect_response": True,
            "include_reply_key": True,
            "response_mappings": [
                {
                    "tokens": ["DONE"],
                    "context_updates": {"forms_status": "complete"},
                },
                {
                    "tokens": ["CALL"],
                    "handoff_reason": "patient_asks_for_staff",
                },
            ],
        },
        {
            "type": "wait",
            "id": "wait-response",
            "delay": {"delay_type": "duration", "duration_seconds": 7200},
            "next_node_id": "check-forms",
        },
        {
            "type": "condition",
            "id": "check-forms",
            "rules": [{"field": "forms_status", "op": "eq", "value": "complete"}],
            "true_next_node_id": "exit-complete",
            "false_next_node_id": "exit-no-response",
        },
        {"type": "exit", "id": "exit-complete", "outcome": "forms_complete"},
        {"type": "exit", "id": "exit-no-response", "outcome": "no_response"},
    ],
}

_SMS_REPLY_WAIT_DEFINITION = {
    "trigger": {"type": "manual"},
    "entry_node_id": "sms-1",
    "nodes": [
        {
            "type": "send_sms",
            "id": "sms-1",
            "body_template": "Reply YES or NO.",
            "next_node_id": "wait-response",
        },
        {
            "type": "wait",
            "id": "wait-response",
            "next_node_id": "check-reply",
            "wait_for": {
                "type": "sms_reply",
                "response_mappings": [
                    {
                        "tokens": ["YES"],
                        "context_updates": {"sms_reply": "yes"},
                    }
                ],
            },
        },
        {
            "type": "condition",
            "id": "check-reply",
            "rules": [{"field": "sms_reply", "op": "eq", "value": "yes"}],
            "true_next_node_id": "exit-yes",
            "false_next_node_id": "exit-no-response",
        },
        {"type": "exit", "id": "exit-yes", "outcome": "yes"},
        {"type": "exit", "id": "exit-no-response", "outcome": "no_response"},
    ],
}

_WAIT_BOOKED_DEFINITION = {
    "trigger": {"type": "recall_scan", "recall_interval_months": 18},
    "entry_node_id": "wait-48h",
    "nodes": [
        {
            "type": "wait",
            "id": "wait-48h",
            "delay": {"delay_type": "duration", "duration_seconds": 172800},
            "next_node_id": "check-booked",
        },
        {
            "type": "condition",
            "id": "check-booked",
            "rules": [{"field": "appointment_booked", "op": "eq", "value": True}],
            "true_next_node_id": "exit-booked",
            "false_next_node_id": "exit-emailed",
        },
        {"type": "exit", "id": "exit-booked", "outcome": "booked"},
        {"type": "exit", "id": "exit-emailed", "outcome": "email_sent"},
    ],
}


# ---------------------------------------------------------------------------
# _retry_countdown
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("retries,expected", [
    (0, 1),
    (1, 2),
    (2, 4),
    (3, 8),
    (8, 256),
    (9, 300),  # capped at 300
])
def test_retry_countdown(retries, expected) -> None:
    assert _retry_countdown(retries) == expected


def test_waiting_step_targets_context_field_is_field_specific() -> None:
    confirm = WorkflowDefinition.model_validate(_WAIT_CONFIRM_DEFINITION)
    booked = WorkflowDefinition.model_validate(_WAIT_BOOKED_DEFINITION)
    sms_reply = WorkflowDefinition.model_validate(_SMS_REPLY_WAIT_DEFINITION)

    assert _waiting_step_targets_field(confirm, "wait-response", "appointment_status")
    assert not _waiting_step_targets_field(confirm, "wait-response", "appointment_booked")
    assert _waiting_step_targets_field(booked, "wait-48h", "appointment_booked")
    assert not _waiting_step_targets_field(booked, "wait-48h", "appointment_status")
    assert _waiting_step_targets_field(sms_reply, "wait-response", "sms_reply")
    assert not _waiting_step_targets_field(sms_reply, "wait-response", "appointment_status")


def test_dial_outcome_for_attempt_maps_business_outcome_to_answered() -> None:
    assert (
        _dial_outcome_for_attempt(
            call_outcome="confirmed",
            disconnection_reason="agent_hangup",
        )
        == "answered"
    )


def test_dial_outcome_for_attempt_preserves_low_level_outcome() -> None:
    assert (
        _dial_outcome_for_attempt(
            call_outcome="no_answer",
            disconnection_reason="agent_hangup",
        )
        == "no_answer"
    )


def test_retell_call_details_outcome_prefers_custom_analysis() -> None:
    details = SimpleNamespace(
        call_status="ended",
        disconnection_reason="agent_hangup",
        call_analysis={
            "custom_analysis_data": {
                "call_outcome": "confirmed",
                "callback_at": "2026-07-02T15:00:00",
                "ignored": "value",
            }
        },
        scrubbed_call_analysis=None,
    )

    assert _retell_call_details_ready_for_resume(details)
    assert _retell_call_details_outcome(details) == "confirmed"


def test_retell_call_details_outcome_falls_back_to_disconnect_reason() -> None:
    details = SimpleNamespace(
        call_status="ended",
        disconnection_reason="agent_hangup",
        call_analysis=None,
        scrubbed_call_analysis=None,
    )

    assert _retell_call_details_ready_for_resume(details)
    assert _retell_call_details_outcome(details) == "answered"


@pytest.mark.asyncio
async def test_gotracker_writeback_sweeper_completes_matching_reschedule() -> None:
    pending = SimpleNamespace(
        id="wb-1",
        action="reschedule",
        requested_start_time=datetime(2026, 8, 15, 11, 0, tzinfo=timezone.utc),
        previous_start_time=datetime(2026, 8, 11, 12, 50, tzinfo=timezone.utc),
        contact_id="contact-1",
        provider_id="gt-2",
        workflow_run_id="run-1",
        status_id=None,
        confirmed=None,
        preconfirmed=None,
    )
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.get = AsyncMock(
        side_effect=[
            SimpleNamespace(id="inst-1", pms_type="gotracker", slug="clinic"),
            SimpleNamespace(id="loc-1", slug="downtown"),
        ]
    )
    writebacks = MagicMock()
    writebacks.get_pending = AsyncMock(return_value=pending)
    writebacks.complete = AsyncMock(return_value=pending)
    writebacks.fail = AsyncMock()
    projection = MagicMock()
    projection.upsert_appointment = AsyncMock()
    adapter = SimpleNamespace(
        get_appointment=AsyncMock(
            return_value={
                "AppointmentId": 1398,
                "AppointmentDate": "2026-08-15",
                "AppointmentTime": "11:00:00",
                "StatusId": 1,
            }
        ),
        close=AsyncMock(),
    )
    cancel_runs = AsyncMock(return_value=1)

    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=session,
    ), patch(
        "src.app.services.automation.gotracker_writeback_service."
        "GoTrackerAppointmentWritebackService",
        return_value=writebacks,
    ), patch(
        "src.app.pms.factory.get_adapter_for_institution_location",
        new=AsyncMock(return_value=adapter),
    ), patch(
        "src.app.services.automation.nexhealth_projection_service.NexHealthProjectionService",
        return_value=projection,
    ), patch(
        "src.app.api.routes.nexhealth_webhooks._cancel_runs_for_appointment",
        new=cancel_runs,
    ), patch(
        "src.app.tasks.automation_workflow.trigger_appointment_workflows"
    ) as trigger_task:
        trigger_task.delay = MagicMock()
        result = await _resolve_gotracker_writeback_target(
            id="wb-1",
            institution_id="inst-1",
            location_id="loc-1",
            appointment_id="gt-1398",
        )

    assert result == {"status": "completed", "reason": "reschedule"}
    writebacks.complete.assert_awaited_once_with(
        pending,
        source_event_id="sweeper:wb-1",
    )
    writebacks.fail.assert_not_called()
    projection.upsert_appointment.assert_awaited_once()
    assert projection.upsert_appointment.await_args.kwargs["start_time"] == (
        "2026-08-15T11:00:00+00:00"
    )
    cancel_runs.assert_awaited_once_with(
        "inst-1",
        "gt-1398",
        reason="gotracker_writeback_sweeper_reschedule",
        include_running=False,
    )
    trigger_task.delay.assert_called_once()
    assert trigger_task.delay.call_args.kwargs["appointment_at_iso"] == (
        "2026-08-15T11:00:00+00:00"
    )
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_trigger_patient_status_schedules_matching_independent_workflow() -> None:
    event = SimpleNamespace(
        id="status-event-1",
        institution_id="inst-1",
        location_id="loc-1",
        contact_id="contact-1",
        workflow_id="preop-wf",
        workflow_run_id="preop-run",
        step_id="mark-confirmed",
        status="appointment_confirmed",
    )
    source_run = SimpleNamespace(
        trigger_metadata={
            "appointment_at": "2026-07-28T10:00:00+00:00",
            "appointment_date": "July 28, 2026",
            "appointment_time": "10:00 AM",
        }
    )
    matching_workflow = SimpleNamespace(
        id="postop-wf",
        current_version_id="postop-ver",
        definition={
            "trigger": {
                "type": "patient_status_changed",
                "statuses": ["appointment_confirmed"],
                "campaign_goal": "post_op_followup",
            },
            "entry_node_id": "exit-1",
            "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
        },
    )
    source_workflow = SimpleNamespace(
        id="preop-wf",
        current_version_id="preop-ver",
        definition=matching_workflow.definition,
    )
    nonmatching_workflow = SimpleNamespace(
        id="other-wf",
        current_version_id="other-ver",
        definition={
            "trigger": {
                "type": "patient_status_changed",
                "statuses": ["post_op_complete"],
            },
            "entry_node_id": "exit-1",
            "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
        },
    )

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    async def _get(_model, pk):
        if pk == "status-event-1":
            return event
        if pk == "preop-run":
            return source_run
        return None

    session.get = _get
    trigger_service = AsyncMock()
    trigger_service.find_active_status_workflows = AsyncMock(
        return_value=[matching_workflow, source_workflow, nonmatching_workflow]
    )

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch(
            "src.app.tasks.automation_workflow.PatientStatusTriggerService",
            return_value=trigger_service,
        ),
        patch("src.app.tasks.automation_workflow.enroll_and_start_workflow_run") as mock_enroll,
    ):
        result = await _trigger_patient_status_async(
            institution_id="inst-1",
            status_event_id="status-event-1",
        )

    assert result == {
        "status_event_id": "status-event-1",
        "status": "appointment_confirmed",
        "scheduled": 1,
        "skipped": 2,
    }
    mock_enroll.apply_async.assert_called_once()
    kwargs = mock_enroll.apply_async.call_args.kwargs["kwargs"]
    assert kwargs["workflow_id"] == "postop-wf"
    assert kwargs["workflow_version_id"] == "postop-ver"
    assert kwargs["contact_id"] == "contact-1"
    assert kwargs["location_id"] == "loc-1"
    assert kwargs["trigger_type"] == "patient_status_changed"
    assert kwargs["trigger_ref_id"] == "status-event-1"
    assert kwargs["idempotency_key"] == "patient-status:postop-ver:status-event-1"
    assert kwargs["trigger_metadata"] == {
        "appointment_at": "2026-07-28T10:00:00+00:00",
        "appointment_date": "July 28, 2026",
        "appointment_time": "10:00 AM",
        "patient_workflow_status": "appointment_confirmed",
        "patient_status": "appointment_confirmed",
        "source_patient_status_event_id": "status-event-1",
        "source_workflow_id": "preop-wf",
        "source_workflow_run_id": "preop-run",
        "source_workflow_step_id": "mark-confirmed",
        "campaign_goal": "post_op_followup",
    }


@pytest.mark.asyncio
async def test_trigger_appointment_state_schedules_matching_confirmed_workflow() -> None:
    matching_workflow = SimpleNamespace(
        id="postop-wf",
        current_version_id="postop-ver",
        definition={
            "trigger": {
                "type": "appointment_state_changed",
                "status_ids": [],
                "confirmed": True,
                "preconfirmed": None,
                "campaign_goal": "post_op_followup",
            },
            "entry_node_id": "exit-1",
            "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
        },
    )
    nonmatching_workflow = SimpleNamespace(
        id="other-wf",
        current_version_id="other-ver",
        definition={
            "trigger": {
                "type": "appointment_state_changed",
                "status_ids": [3],
                "confirmed": None,
                "preconfirmed": None,
            },
            "entry_node_id": "exit-1",
            "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
        },
    )
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    trigger_service = AsyncMock()
    trigger_service.find_active_appointment_state_workflows = AsyncMock(
        return_value=[matching_workflow, nonmatching_workflow]
    )
    trigger_service.get_appointment_context = AsyncMock(
        return_value={
            "appointment_at": "2026-07-28T10:00:00+00:00",
            "contact_id": "contact-from-projection",
            "location_id": "loc-from-projection",
            "appointment_status": "scheduled",
        }
    )

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch(
            "src.app.tasks.automation_workflow.AppointmentTriggerService",
            return_value=trigger_service,
        ),
        patch("src.app.tasks.automation_workflow.enroll_and_start_workflow_run") as mock_enroll,
    ):
        result = await _trigger_appointment_state_async(
            institution_id="inst-1",
            appointment_id="gt-1343",
            contact_id=None,
            location_id=None,
            status_id=1,
            confirmed=True,
            preconfirmed=False,
            trigger_metadata={"source": "workflow_gotracker_writeback"},
        )

    assert result == {"appointment_id": "gt-1343", "scheduled": 1, "skipped": 1}
    mock_enroll.apply_async.assert_called_once()
    kwargs = mock_enroll.apply_async.call_args.kwargs["kwargs"]
    assert kwargs["workflow_id"] == "postop-wf"
    assert kwargs["workflow_version_id"] == "postop-ver"
    assert kwargs["contact_id"] == "contact-from-projection"
    assert kwargs["location_id"] == "loc-from-projection"
    assert kwargs["trigger_type"] == "appointment_state_changed"
    assert kwargs["trigger_ref_type"] == "appointment"
    assert kwargs["trigger_ref_id"] == "gt-1343"
    assert kwargs["idempotency_key"] == (
        "appt-state:postop-ver:gt-1343:status=1:confirmed=True:preconfirmed=False"
    )
    assert kwargs["trigger_metadata"]["appointment_at"] == "2026-07-28T10:00:00+00:00"
    assert kwargs["trigger_metadata"]["is_confirmed"] is True
    assert kwargs["trigger_metadata"]["is_preconfirmed"] is False
    assert kwargs["trigger_metadata"]["campaign_goal"] == "post_op_followup"


@pytest.mark.asyncio
async def test_trigger_appointment_state_schedules_completed_flow_with_deadline() -> None:
    workflow = SimpleNamespace(
        id="postop-wf",
        current_version_id="postop-ver",
        definition={
            "trigger": {
                "type": "appointment_state_changed",
                "flow_states": ["Completed"],
                "max_followup_delay_hours": 72,
                "campaign_goal": "post_op_followup",
            },
            "entry_node_id": "exit-1",
            "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
        },
    )
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    trigger_service = AsyncMock()
    trigger_service.find_active_appointment_state_workflows = AsyncMock(return_value=[workflow])
    trigger_service.get_appointment_context = AsyncMock(
        return_value={
            "appointment_reason": "Implant Surgery",
            "contact_id": "contact-from-projection",
            "location_id": "loc-from-projection",
        }
    )

    with (
        patch("src.app.tasks.automation_workflow.get_system_db_session", return_value=session),
        patch("src.app.tasks.automation_workflow.AppointmentTriggerService", return_value=trigger_service),
        patch("src.app.tasks.automation_workflow.enroll_and_start_workflow_run") as mock_enroll,
    ):
        result = await _trigger_appointment_state_async(
            institution_id="inst-1",
            appointment_id="gt-1414",
            contact_id=None,
            location_id=None,
            status_id=1,
            confirmed=True,
            preconfirmed=False,
            flow_state="Completed",
            flow_changed_at="2026-08-12T09:27:01.940Z",
            trigger_metadata={"source": "gotracker_webhook"},
        )

    assert result == {"appointment_id": "gt-1414", "scheduled": 1, "skipped": 0}
    kwargs = mock_enroll.apply_async.call_args.kwargs["kwargs"]
    assert kwargs["idempotency_key"] == (
        "appt-state:postop-ver:gt-1414:status=1:confirmed=True:preconfirmed=False:"
        "flow_state=Completed:flow_changed_at=2026-08-12T09:27:01.940Z"
    )
    assert kwargs["trigger_metadata"]["appointment_reason"] == "Implant Surgery"
    assert kwargs["trigger_metadata"]["flow_changed_at"] == "2026-08-12T09:27:01.940Z"
    assert kwargs["trigger_metadata"]["post_op_expires_at"] == "2026-08-15T09:27:01.940000+00:00"


@pytest.mark.asyncio
async def test_poll_retell_voice_outcomes_enqueues_resume_for_completed_call() -> None:
    attempt = SimpleNamespace(
        institution_id="inst-1",
        retell_call_id="call_abc",
    )
    details = SimpleNamespace(
        call_status="ended",
        disconnection_reason="agent_hangup",
        call_analysis={
            "custom_analysis_data": {
                "call_outcome": "confirmed",
                "callback_at": "2026-07-02T15:00:00",
            }
        },
        scrubbed_call_analysis=None,
    )

    result_proxy = MagicMock()
    result_proxy.scalars.return_value.all.return_value = [attempt]
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=result_proxy)
    mock_client = AsyncMock()
    mock_client.get_phone_call = AsyncMock(return_value=details)

    with (
        patch(
            "src.app.tasks.automation_workflow._superadmin_system_session",
            return_value=mock_session,
        ) as superadmin_session,
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            side_effect=AssertionError("cross-tenant poll must not use an unscoped session"),
        ),
        patch(
            "src.app.services.automation.retell_outbound_client.RetellOutboundClient",
            return_value=mock_client,
        ),
        patch.object(settings, "retell_api_secret", "re_test_key"),
        patch("src.app.tasks.automation_workflow.resume_voice_outcome") as mock_resume,
    ):
        result = await _poll_retell_voice_outcomes_async()

    assert result == {"scanned": 1, "enqueued": 1, "pending": 0, "failed": 0}
    superadmin_session.assert_called_once_with("retell_voice_outcome_poll")
    mock_resume.apply_async.assert_called_once_with(
        kwargs={
            "institution_id": "inst-1",
            "retell_call_id": "call_abc",
            "call_outcome": "confirmed",
            "disconnection_reason": "agent_hangup",
            "outcome_context": {"callback_at": "2026-07-02T15:00:00"},
        },
        queue="workflow",
    )


# ---------------------------------------------------------------------------
# _claim_and_enqueue_async
# ---------------------------------------------------------------------------


def _make_timer(timer_id="t-1", institution_id="inst-1", location_id=None, run_id="run-1"):
    t = MagicMock()
    t.id = timer_id
    t.institution_id = institution_id
    t.location_id = location_id
    t.workflow_run_id = run_id
    return t


@pytest.mark.asyncio
async def test_claim_and_enqueue_no_timers() -> None:
    mock_svc = AsyncMock()
    mock_svc.claim_due_timers = AsyncMock(return_value=[])
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.AutomationWorkflowSchedulerService",
            return_value=mock_svc,
        ),
        patch(
            "src.app.tasks.automation_workflow._superadmin_system_session",
            return_value=mock_session,
        ) as superadmin_session,
        patch(
            "src.app.tasks.automation_workflow.dispatch_workflow_timer",
        ) as mock_dispatch,
    ):
        result = await _claim_and_enqueue_async()

    assert result == {"claimed": 0, "rounds": 1, "remaining": 0}
    superadmin_session.assert_called_once_with("workflow_scheduler_poll")
    mock_dispatch.apply_async.assert_not_called()


@pytest.mark.asyncio
async def test_claim_and_enqueue_enqueues_per_timer() -> None:
    timers = [
        _make_timer("t-1", "inst-1", None, "run-1"),
        _make_timer("t-2", "inst-2", "loc-1", "run-2"),
    ]
    mock_svc = AsyncMock()
    mock_svc.claim_due_timers = AsyncMock(return_value=timers)
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.AutomationWorkflowSchedulerService",
            return_value=mock_svc,
        ),
        patch(
            "src.app.tasks.automation_workflow._superadmin_system_session",
            return_value=mock_session,
        ) as superadmin_session,
        patch(
            "src.app.tasks.automation_workflow.dispatch_workflow_timer",
        ) as mock_dispatch,
    ):
        result = await _claim_and_enqueue_async()

    # One round: a short batch means the backlog is drained, so it stops.
    assert result == {"claimed": 2, "rounds": 1, "remaining": 0}
    superadmin_session.assert_called_once_with("workflow_scheduler_poll")
    assert mock_dispatch.apply_async.call_count == 2


# ---------------------------------------------------------------------------
# _dispatch_timer_async — timer not found / not claimed
# ---------------------------------------------------------------------------


def _mock_session_get(return_map: dict):
    """Build an AsyncSession where session.get(Model, pk) returns from return_map."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.flush = AsyncMock()

    async def _get(model, pk, **kwargs):
        return return_map.get((model, pk))

    session.get = _get

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)
    return session


def _result(*, scalars_all=None, scalar_one_or_none=None):
    result = MagicMock()
    result.scalars.return_value.all.return_value = scalars_all or []
    result.scalar_one_or_none.return_value = scalar_one_or_none
    return result


@pytest.mark.asyncio
async def test_sms_response_mapping_updates_context_and_resumes_one_run() -> None:
    run = MagicMock()
    run.id = "run-1"
    run.institution_id = "inst-1"
    run.location_id = "loc-1"
    run.workflow_version_id = "ver-1"
    run.current_step_id = "wait-response"
    run.status = AutomationRunStatus.WAITING.value
    run.trigger_metadata = {"source": "test"}
    version = MagicMock()
    version.definition = _SMS_MAPPING_DEFINITION
    event = MagicMock()
    event.id = "event-1"
    session = _mock_session_get({
        (AutomationWorkflowRun, "run-1"): run,
        (AutomationWorkflowVersion, "ver-1"): version,
    })
    session.execute = AsyncMock(
        side_effect=[
            _result(scalars_all=["contact-1"]),
            _result(scalar_one_or_none=event),
            _result(scalar_one_or_none=run),
        ]
    )

    mapping = WorkflowDefinition.model_validate(_SMS_MAPPING_DEFINITION).nodes[0].response_mappings[0]
    match = SimpleNamespace(node_id="sms-1", mapping=mapping)
    dispatcher = AsyncMock()
    from src.app.services.automation.step_dispatcher import DispatchResult

    dispatcher.resume_after_timer = AsyncMock(
        return_value=DispatchResult(status="completed", outcome="forms_complete")
    )

    with (
        patch("src.app.tasks.automation_workflow.get_system_db_session", return_value=session),
        patch("src.app.tasks.automation_workflow.build_dispatcher", new=AsyncMock(return_value=(dispatcher, "UTC"))),
        patch("src.app.tasks.automation_workflow.AutomationWorkflowSchedulerService") as Scheduler,
        patch("src.app.tasks.automation_workflow._confirm_appointments_for_runs", new=AsyncMock()) as confirm,
        patch("src.app.services.automation.campaign_conversation_service.CampaignConversationService.match_sms_response_mapping", new=AsyncMock(return_value=match)),
    ):
        Scheduler.return_value.cancel_timers_for_run = AsyncMock(return_value=1)
        result = await _resume_sms_confirmation_async(
            institution_id="inst-1",
            location_id="loc-1",
            from_number="+14165551234",
            body="DONE R2ABCD",
            message_sid="SM123",
            workflow_run_id="run-1",
            conversation_thread_id="thread-1",
        )

    assert result["resumed"] == 1
    assert result["outcomes"] == {"forms_complete": 1}
    assert run.trigger_metadata["forms_status"] == "complete"
    assert run.trigger_metadata["sms_response_node_id"] == "sms-1"
    assert run.trigger_metadata["last_campaign_response_event_id"] == "event-1"
    dispatcher.resume_after_timer.assert_awaited_once()
    confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_sms_response_mapping_with_handoff_only_does_not_resume() -> None:
    session = _mock_session_get({})
    session.execute = AsyncMock(
        side_effect=[
            _result(scalars_all=["contact-1"]),
            _result(scalar_one_or_none=None),
        ]
    )
    mapping = WorkflowDefinition.model_validate(_SMS_MAPPING_DEFINITION).nodes[0].response_mappings[1]
    match = SimpleNamespace(node_id="sms-1", mapping=mapping)

    with (
        patch("src.app.tasks.automation_workflow.get_system_db_session", return_value=session),
        patch("src.app.tasks.automation_workflow.build_dispatcher", new=AsyncMock()) as build,
        patch("src.app.services.automation.campaign_conversation_service.CampaignConversationService.match_sms_response_mapping", new=AsyncMock(return_value=match)),
    ):
        result = await _resume_sms_confirmation_async(
            institution_id="inst-1",
            location_id="loc-1",
            from_number="+14165551234",
            body="CALL R2ABCD",
            message_sid="SM123",
            workflow_run_id="run-1",
            conversation_thread_id="thread-1",
        )

    assert result["resumed"] == 0
    assert result["matched"] == 1
    assert result["reason"] == "mapping_created_handoff"
    build.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_timer_not_found_skips() -> None:
    session = _mock_session_get({})

    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=session,
    ):
        result = await _dispatch_timer_async(
            timer_id="t-1",
            institution_id="inst-1",
            location_id=None,
            run_id="run-1",
        )

    assert result["skipped"] is True
    assert "timer not claimed" in result["reason"]


@pytest.mark.asyncio
async def test_dispatch_timer_run_not_advanceable_fires_timer() -> None:
    timer = MagicMock()
    timer.id = "t-1"
    timer.status = AutomationTimerStatus.CLAIMED.value

    run = MagicMock()
    run.id = "run-1"
    run.status = AutomationRunStatus.COMPLETED.value  # terminal — not advanceable

    session = _mock_session_get({
        (AutomationWorkflowTimer, "t-1"): timer,
        (AutomationWorkflowRun, "run-1"): run,
    })

    mock_sched = AsyncMock()
    mock_sched.fire_timer = AsyncMock()

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch(
            "src.app.tasks.automation_workflow.AutomationWorkflowSchedulerService",
            return_value=mock_sched,
        ),
    ):
        result = await _dispatch_timer_async(
            timer_id="t-1",
            institution_id="inst-1",
            location_id=None,
            run_id="run-1",
        )

    assert result["skipped"] is True
    assert "not advanceable" in result["reason"]
    mock_sched.fire_timer.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_timer_version_missing_skips() -> None:
    timer = MagicMock()
    timer.id = "t-1"
    timer.status = AutomationTimerStatus.CLAIMED.value

    run = MagicMock()
    run.id = "run-1"
    run.status = AutomationRunStatus.WAITING.value
    run.workflow_version_id = "ver-1"
    run.location_id = None

    session = _mock_session_get({
        (AutomationWorkflowTimer, "t-1"): timer,
        (AutomationWorkflowRun, "run-1"): run,
        # version deliberately absent
    })

    with patch(
        "src.app.tasks.automation_workflow.get_system_db_session",
        return_value=session,
    ):
        result = await _dispatch_timer_async(
            timer_id="t-1",
            institution_id="inst-1",
            location_id=None,
            run_id="run-1",
        )

    assert result["skipped"] is True
    assert "version not found" in result["reason"]


@pytest.mark.asyncio
async def test_dispatch_timer_happy_path_returns_dispatch_result() -> None:
    timer = MagicMock()
    timer.id = "t-1"
    timer.status = AutomationTimerStatus.CLAIMED.value

    run = MagicMock()
    run.id = "run-1"
    run.status = AutomationRunStatus.WAITING.value
    run.workflow_version_id = "ver-1"
    run.location_id = None
    run.trigger_metadata = {}
    run.current_step_id = "wait-1"

    version = MagicMock()
    version.definition = _VALID_DEFINITION

    session = _mock_session_get({
        (AutomationWorkflowTimer, "t-1"): timer,
        (AutomationWorkflowRun, "run-1"): run,
        (AutomationWorkflowVersion, "ver-1"): version,
    })

    from src.app.services.automation.step_dispatcher import DispatchResult

    mock_dispatcher = AsyncMock()
    mock_dispatcher.scheduler = AsyncMock()
    mock_dispatcher.scheduler.fire_timer = AsyncMock()
    mock_dispatcher.resume_after_timer = AsyncMock(
        return_value=DispatchResult(status="completed", outcome="done", steps_advanced=1)
    )

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch(
            "src.app.tasks.automation_workflow.build_dispatcher",
            new=AsyncMock(return_value=(mock_dispatcher, "UTC")),
        ),
    ):
        result = await _dispatch_timer_async(
            timer_id="t-1",
            institution_id="inst-1",
            location_id=None,
            run_id="run-1",
        )

    assert result["dispatch_status"] == "completed"
    assert result["outcome"] == "done"
    assert result["steps_advanced"] == 1
    mock_dispatcher.scheduler.fire_timer.assert_awaited_once()
    mock_dispatcher.resume_after_timer.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_timer_defers_when_workflow_paused() -> None:
    """A waiting run whose workflow is paused is deferred (timer re-armed), not
    advanced — pause stops in-flight runs, not just new enrollments."""
    from src.app.models.automation_workflow import (
        AutomationWorkflow,
        AutomationWorkflowStatus,
    )

    timer = MagicMock()
    timer.id = "t-1"
    timer.status = AutomationTimerStatus.CLAIMED.value

    run = MagicMock()
    run.id = "run-1"
    run.status = AutomationRunStatus.WAITING.value
    run.workflow_id = "wf-1"
    run.location_id = None

    workflow = MagicMock()
    workflow.status = AutomationWorkflowStatus.PAUSED.value

    session = _mock_session_get({
        (AutomationWorkflowTimer, "t-1"): timer,
        (AutomationWorkflowRun, "run-1"): run,
        (AutomationWorkflow, "wf-1"): workflow,
    })

    with (
        patch(
            "src.app.tasks.automation_workflow.get_system_db_session",
            return_value=session,
        ),
        patch("src.app.tasks.automation_workflow.build_dispatcher") as mock_build,
    ):
        result = await _dispatch_timer_async(
            timer_id="t-1", institution_id="inst-1", location_id=None, run_id="run-1"
        )

    assert result["skipped"] is True
    assert result["reason"] == "workflow paused"
    assert result.get("deferred") is True
    mock_build.assert_not_called()
    assert timer.status == AutomationTimerStatus.PENDING.value  # re-armed, not fired


@pytest.mark.asyncio
async def test_confirm_appointments_uses_pms_factory_for_gotracker() -> None:
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation

    institution = SimpleNamespace(id="inst-1", pms_type="gotracker", slug="clinic")
    location = SimpleNamespace(id="loc-1", slug="downtown")
    run = SimpleNamespace(
        id="run-1",
        trigger_ref_type="appointment",
        trigger_ref_id="gt-1343",
    )
    adapter = _FakeConfirmAdapter()
    session = _mock_session_get(
        {
            (Institution, "inst-1"): institution,
            (InstitutionLocation, "loc-1"): location,
        }
    )

    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ) as mock_factory,
        patch("src.app.services.audit.log_audit", new=AsyncMock()) as mock_audit,
    ):
        await _confirm_appointments_for_runs(
            session,
            institution_id="inst-1",
            location_id="loc-1",
            runs=[run],
        )

    mock_factory.assert_awaited_once_with(institution, location)
    assert adapter.confirmed == ["gt-1343"]
    assert adapter.closed is True
    mock_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_stale_async_returns_count() -> None:
    """The stale-claim recovery task delegates to the scheduler and reports count."""
    from src.app.tasks.automation_workflow import _recover_stale_async

    session = _mock_session_get({})
    with (
        patch(
            "src.app.tasks.automation_workflow._superadmin_system_session",
            return_value=session,
        ) as superadmin_session,
        patch(
            "src.app.tasks.automation_workflow.AutomationWorkflowSchedulerService"
        ) as mock_cls,
    ):
        mock_cls.return_value.recover_stale_claims = AsyncMock(return_value=3)
        result = await _recover_stale_async()

    assert result["recovered"] == 3
    superadmin_session.assert_called_once_with("workflow_stale_recovery")
    mock_cls.return_value.recover_stale_claims.assert_awaited_once()
