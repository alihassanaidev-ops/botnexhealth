"""Unit tests for WorkflowStepDispatcher node dispatch and condition evaluation."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from httpx import Response

from src.app.config import settings
from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationWorkflowDripState,
    AutomationWorkflowRun,
    AutomationWorkflowStepExecution,
    AutomationStepStatus,
)
from src.app.services.automation.definition_schema import (
    AppointmentRelativeDelay,
    AppointmentOffsetTrigger,
    CalendarDelay,
    ConditionNode,
    ConditionRule,
    DripNode,
    DurationDelay,
    ExitNode,
    JsonMapperNode,
    JsonMapping,
    LlmLabelRule,
    LlmNode,
    SendSmsNode,
    SmsReplyWaitConfig,
    UpdateGoTrackerAppointmentNode,
    UpdatePatientStatusNode,
    WaitNode,
    WorkflowDefinition,
)
from src.app.services.automation.step_dispatcher import (
    WorkflowStepDispatcher,
    _compute_due_at,
    _evaluate_condition,
    _evaluate_rule,
)
from src.app.services.automation.llm_node_executor import execute_llm_node
from src.app.services.automation.campaign_templates import TEMPLATES, instantiate_definition

_NOW = datetime(2026, 7, 2, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(status: str = AutomationRunStatus.RUNNING.value) -> AutomationWorkflowRun:
    return AutomationWorkflowRun(
        institution_id="inst-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        status=status,
    )


def _make_step(
    status: str = AutomationStepStatus.WAITING.value,
    *,
    step_id: str = "wait-1",
    step_type: str = "wait",
) -> AutomationWorkflowStepExecution:
    return AutomationWorkflowStepExecution(
        institution_id="inst-1",
        workflow_run_id="run-1",
        workflow_version_id="ver-1",
        step_id=step_id,
        step_type=step_type,
        status=status,
    )


def _make_runtime() -> AsyncMock:
    rt = AsyncMock()
    step = MagicMock()
    step.id = "step-exec-1"
    step.step_id = "step-1"
    rt.begin_step = AsyncMock(return_value=step)
    rt.complete_step = AsyncMock(return_value=step)
    rt.wait_run = AsyncMock()
    rt.complete_run = AsyncMock()
    rt.fail_run = AsyncMock()
    rt.resume_run = AsyncMock()
    return rt


class _FakeGoTrackerWritebackAdapter:
    source = "gotracker"

    def __init__(self) -> None:
        self.set_appointment_status_id = AsyncMock(
            return_value=SimpleNamespace(success=True, status="status_updated", error=None)
        )
        self.update_appointment = AsyncMock(
            return_value=SimpleNamespace(success=True, status="appointment_updated", error=None)
        )
        self.close = AsyncMock()


def _make_scheduler() -> AsyncMock:
    sched = AsyncMock()
    timer = MagicMock()
    timer.id = "timer-1"
    sched.create_timer = AsyncMock(return_value=timer)
    sched.fire_timer = AsyncMock()
    return sched


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)
    return session


def _definition(nodes: list, entry: str, trigger=None) -> WorkflowDefinition:
    return WorkflowDefinition(
        trigger=trigger or AppointmentOffsetTrigger(offset_hours=-24),
        entry_node_id=entry,
        nodes=nodes,
    )


# ---------------------------------------------------------------------------
# advance() — send_sms → exit
# ---------------------------------------------------------------------------


def test_advance_sms_to_exit_returns_completed() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            SendSmsNode(id="sms-1", body_template="Hi", next_node_id="exit-1"),
            ExitNode(id="exit-1", outcome="sent"),
        ],
        entry="sms-1",
    )

    result = asyncio.run(dispatcher.advance(run, defn, context={}))

    assert result.status == "completed"
    assert result.outcome == "sent"
    assert result.steps_advanced == 2
    rt.complete_run.assert_awaited_once()


def test_advance_json_mapper_writes_context_for_condition() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            JsonMapperNode(
                id="map-1",
                mappings=[
                    JsonMapping(
                        source_path="gotracker_payload.appointment.reasons.0",
                        target_field="appointment_reason",
                    )
                ],
                next_node_id="cond-1",
            ),
            ConditionNode(
                id="cond-1",
                rules=[
                    ConditionRule(
                        field="appointment_reason",
                        op="contains",
                        value="implant",
                    )
                ],
                true_next_node_id="exit-yes",
                false_next_node_id="exit-no",
            ),
            ExitNode(id="exit-yes", outcome="implant"),
            ExitNode(id="exit-no", outcome="other"),
        ],
        entry="map-1",
    )

    result = asyncio.run(
        dispatcher.advance(
            run,
            defn,
            context={"gotracker_payload": {"appointment": {"reasons": ["Implant surgery"]}}},
        )
    )

    assert result.status == "completed"
    assert result.outcome == "implant"
    rt.complete_step.assert_any_await(
        rt.begin_step.return_value,
        result_code="mapped",
        result_metadata={"mapped_fields": {"appointment_reason": "Implant surgery"}},
    )


def test_advance_gotracker_writeback_records_pending_reschedule() -> None:
    session = _make_session()
    session.get = AsyncMock(
        side_effect=[
            SimpleNamespace(id="inst-1", pms_type="gotracker", slug="clinic"),
            SimpleNamespace(id="loc-1", slug="downtown"),
        ]
    )
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)
    adapter = _FakeGoTrackerWritebackAdapter()
    writeback_svc = MagicMock()
    writeback_svc.acquire_appointment_lock = AsyncMock()
    writeback_svc.pending_for_appointment = AsyncMock(return_value=None)
    writeback_svc.record_request = AsyncMock()

    run = _make_run()
    run.id = "run-1"
    run.location_id = "loc-1"
    run.trigger_ref_type = "appointment"
    run.trigger_ref_id = "gt-1343"
    defn = _definition(
        nodes=[
            UpdateGoTrackerAppointmentNode(
                id="gt-write",
                next_node_id="exit-1",
                start_time="{{new_start_time}}",
                duration_min=45,
                provider_id="{{provider_id}}",
            ),
            ExitNode(id="exit-1", outcome="updated"),
        ],
        entry="gt-write",
    )

    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ),
        patch("src.app.services.audit.log_audit", new=AsyncMock()) as mock_audit,
        patch(
            "src.app.services.automation.gotracker_writeback_service."
            "GoTrackerAppointmentWritebackService",
            return_value=writeback_svc,
        ),
    ):
        result = asyncio.run(
            dispatcher.advance(
                run,
                defn,
                context={
                    "new_start_time": "2026-08-12T14:30",
                    "provider_id": "gt-2",
                },
            )
        )

    assert result.status == "completed"
    writeback_svc.acquire_appointment_lock.assert_awaited_once_with(
        institution_id="inst-1",
        appointment_id="gt-1343",
    )
    writeback_svc.pending_for_appointment.assert_awaited_once_with(
        institution_id="inst-1",
        appointment_id="gt-1343",
    )
    adapter.set_appointment_status_id.assert_not_called()
    adapter.update_appointment.assert_awaited_once_with(
        "gt-1343",
        start_time="2026-08-12T14:30",
        end_time=None,
        duration_min=45,
        provider_id="gt-2",
        operatory_id=None,
        patient_id=None,
        reason=None,
    )
    adapter.close.assert_awaited_once()
    writeback_svc.record_request.assert_awaited_once_with(
        institution_id="inst-1",
        appointment_id="gt-1343",
        location_id="loc-1",
        contact_id=None,
        workflow_run_id="run-1",
        step_id="gt-write",
        action="reschedule",
        requested_start_time="2026-08-12T14:30:00+00:00",
        provider_id="gt-2",
        status_id=None,
        confirmed=None,
        preconfirmed=None,
    )
    assert mock_audit.await_count == 1


def test_advance_gotracker_writeback_strips_slot_offset_without_converting() -> None:
    session = _make_session()
    session.get = AsyncMock(
        side_effect=[
            SimpleNamespace(id="inst-1", pms_type="gotracker", slug="clinic"),
            SimpleNamespace(id="loc-1", slug="downtown", timezone="America/New_York"),
        ]
    )
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)
    adapter = _FakeGoTrackerWritebackAdapter()
    writeback_svc = MagicMock()
    writeback_svc.acquire_appointment_lock = AsyncMock()
    writeback_svc.pending_for_appointment = AsyncMock(return_value=None)
    writeback_svc.record_request = AsyncMock()

    run = _make_run()
    run.id = "run-1"
    run.location_id = "loc-1"
    run.trigger_ref_type = "appointment"
    run.trigger_ref_id = "gt-1343"
    defn = _definition(
        nodes=[
            UpdateGoTrackerAppointmentNode(
                id="gt-write",
                next_node_id="exit-1",
                start_time="{{slot_start}}",
                duration_min=15,
                provider_id="{{provider_id}}",
                operatory_id="{{operatory_id}}",
            ),
            ExitNode(id="exit-1", outcome="updated"),
        ],
        entry="gt-write",
    )

    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ),
        patch("src.app.services.audit.log_audit", new=AsyncMock()),
        patch(
            "src.app.services.automation.gotracker_writeback_service."
            "GoTrackerAppointmentWritebackService",
            return_value=writeback_svc,
        ),
    ):
        result = asyncio.run(
            dispatcher.advance(
                run,
                defn,
                context={
                    "slot_start": "2026-08-13T09:30:00-04:00",
                    "provider_id": "gt-2",
                    "operatory_id": "gt-7",
                },
            )
        )

    assert result.status == "completed"
    adapter.update_appointment.assert_awaited_once_with(
        "gt-1343",
        start_time="2026-08-13T09:30",
        end_time=None,
        duration_min=15,
        provider_id="gt-2",
        operatory_id="gt-7",
        patient_id=None,
        reason=None,
    )
    writeback_svc.record_request.assert_awaited_once_with(
        institution_id="inst-1",
        appointment_id="gt-1343",
        location_id="loc-1",
        contact_id=None,
        workflow_run_id="run-1",
        step_id="gt-write",
        action="reschedule",
        requested_start_time="2026-08-13T13:30:00+00:00",
        provider_id="gt-2",
        status_id=None,
        confirmed=None,
        preconfirmed=None,
    )


def test_advance_gotracker_writeback_blocks_when_same_appointment_pending() -> None:
    session = _make_session()
    session.get = AsyncMock(
        side_effect=[
            SimpleNamespace(id="inst-1", pms_type="gotracker", slug="clinic"),
            SimpleNamespace(id="loc-1", slug="downtown"),
        ]
    )
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)
    adapter = _FakeGoTrackerWritebackAdapter()
    pending = SimpleNamespace(id="wb-1", action="reschedule")
    writeback_svc = MagicMock()
    writeback_svc.acquire_appointment_lock = AsyncMock()
    writeback_svc.pending_for_appointment = AsyncMock(return_value=pending)
    writeback_svc.record_request = AsyncMock()

    run = _make_run()
    run.id = "run-1"
    run.location_id = "loc-1"
    run.trigger_ref_type = "appointment"
    run.trigger_ref_id = "gt-1343"
    defn = _definition(
        nodes=[
            UpdateGoTrackerAppointmentNode(
                id="gt-write",
                next_node_id="exit-1",
                start_time="{{new_start_time}}",
            ),
            ExitNode(id="exit-1", outcome="updated"),
        ],
        entry="gt-write",
    )

    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.services.automation.gotracker_writeback_service."
            "GoTrackerAppointmentWritebackService",
            return_value=writeback_svc,
        ),
    ):
        result = asyncio.run(
            dispatcher.advance(
                run,
                defn,
                context={"new_start_time": "2026-08-12T14:30"},
            )
        )

    assert result.status == "failed"
    adapter.set_appointment_status_id.assert_not_called()
    adapter.update_appointment.assert_not_called()
    writeback_svc.record_request.assert_not_called()
    rt.fail_step.assert_awaited_once()
    assert rt.fail_step.await_args.kwargs["result_code"] == "gotracker_writeback_failed"
    assert rt.fail_step.await_args.kwargs["result_metadata"] == {
        "pending_writeback_id": "wb-1",
        "pending_action": "reschedule",
    }


def test_advance_gotracker_writeback_rejects_combined_status_and_reschedule() -> None:
    session = _make_session()
    session.get = AsyncMock(
        side_effect=[
            SimpleNamespace(id="inst-1", pms_type="gotracker", slug="clinic"),
            SimpleNamespace(id="loc-1", slug="downtown"),
        ]
    )
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)
    adapter = _FakeGoTrackerWritebackAdapter()
    writeback_svc = MagicMock()
    writeback_svc.acquire_appointment_lock = AsyncMock()
    writeback_svc.pending_for_appointment = AsyncMock(return_value=None)
    writeback_svc.record_request = AsyncMock()

    run = _make_run()
    run.id = "run-1"
    run.location_id = "loc-1"
    run.trigger_ref_type = "appointment"
    run.trigger_ref_id = "gt-1343"
    defn = _definition(
        nodes=[
            UpdateGoTrackerAppointmentNode(
                id="gt-write",
                next_node_id="exit-1",
                status_id=5,
                start_time="{{new_start_time}}",
            ),
            ExitNode(id="exit-1", outcome="updated"),
        ],
        entry="gt-write",
    )

    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ),
        patch(
            "src.app.services.automation.gotracker_writeback_service."
            "GoTrackerAppointmentWritebackService",
            return_value=writeback_svc,
        ),
    ):
        result = asyncio.run(
            dispatcher.advance(
                run,
                defn,
                context={"new_start_time": "2026-08-12T14:30"},
            )
        )

    assert result.status == "failed"
    adapter.set_appointment_status_id.assert_not_called()
    adapter.update_appointment.assert_not_called()
    writeback_svc.record_request.assert_not_called()
    rt.fail_step.assert_awaited_once()
    assert rt.fail_step.await_args.kwargs["result_code"] == "gotracker_writeback_failed"


def test_advance_llm_node_classifies_with_keyword_rules() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            LlmNode(
                id="llm-1",
                source_field="appointment_reasons",
                output_field="appointment_category",
                prompt_template="Classify the appointment reason.",
                labels=["implant", "hygiene", "other"],
                label_rules=[LlmLabelRule(label="implant", keywords=["implant", "surgery"])],
                fallback_label="other",
                allow_keyword_fallback=True,
                next_node_id="cond-1",
            ),
            ConditionNode(
                id="cond-1",
                rules=[ConditionRule(field="appointment_category", op="eq", value="implant")],
                true_next_node_id="exit-yes",
                false_next_node_id="exit-no",
            ),
            ExitNode(id="exit-yes", outcome="implant"),
            ExitNode(id="exit-no", outcome="other"),
        ],
        entry="llm-1",
    )

    result = asyncio.run(
        dispatcher.advance(
            run,
            defn,
            context={"appointment_reasons": ["Surgical implant consult"]},
        )
    )

    assert result.status == "completed"
    assert result.outcome == "implant"
    rt.complete_step.assert_any_await(
        rt.begin_step.return_value,
        result_code="classified",
        result_metadata={
            "provider": "keyword_fallback",
            "source_field": "appointment_reasons",
            "output_field": "appointment_category",
            "label": "implant",
            "fallback_reason": "OPENAI_API_KEY is not configured",
        },
    )


@pytest.mark.asyncio
async def test_execute_llm_node_calls_openai_and_writes_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.openai.test/v1")
    monkeypatch.setattr(settings, "workflow_llm_default_model", "gpt-5.6-luna")

    node = LlmNode(
        id="llm-1",
        source_field="appointment_reasons",
        output_field="appointment_category",
        prompt_template="Classify the appointment reason.",
        labels=["implant", "hygiene", "other"],
        next_node_id="exit-1",
    )
    context = {"appointment_reasons": ["Implant consult"]}

    with respx.mock(base_url="https://api.openai.test") as router:
        route = router.post("/v1/responses").mock(
            return_value=Response(
                200,
                json={
                    "id": "resp-1",
                    "output_text": "{\"value\":\"implant\"}",
                    "usage": {"input_tokens": 20, "output_tokens": 4},
                },
            )
        )

        result = await execute_llm_node(node, context)

    assert route.called
    request_body = json.loads(route.calls[0].request.content)
    assert request_body["model"] == "gpt-5.6-luna"
    assert context["appointment_category"] == "implant"
    assert result.value == "implant"
    assert result.metadata["provider"] == "openai"
    assert result.metadata["response_id"] == "resp-1"


# ---------------------------------------------------------------------------
# advance() — wait node creates timer and pauses
# ---------------------------------------------------------------------------


def test_advance_wait_node_returns_waiting() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            WaitNode(
                id="wait-1",
                delay=DurationDelay(duration_seconds=3600),
                next_node_id="exit-1",
            ),
            ExitNode(id="exit-1"),
        ],
        entry="wait-1",
    )

    result = asyncio.run(dispatcher.advance(run, defn, context={}, now=_NOW))

    assert result.status == "waiting"
    assert result.timer_id == "timer-1"
    sched.create_timer.assert_awaited_once()
    rt.wait_run.assert_awaited_once()


def test_advance_sms_reply_wait_mode_creates_timeout_timer() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            WaitNode(
                id="wait-sms-1",
                wait_for=SmsReplyWaitConfig(response_window_seconds=1800),
                next_node_id="exit-1",
            ),
            ExitNode(id="exit-1"),
        ],
        entry="wait-sms-1",
    )

    result = asyncio.run(dispatcher.advance(run, defn, context={}, now=_NOW))

    assert result.status == "waiting"
    assert result.timer_id == "timer-1"
    assert sched.create_timer.await_args.kwargs["due_at"] == _NOW + timedelta(minutes=30)
    rt.wait_run.assert_awaited_once()


def test_advance_appointment_relative_wait_uses_appointment_at_context() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    appt_at = datetime(2026, 7, 3, 14, 0, 0, tzinfo=timezone.utc)
    defn = _definition(
        nodes=[
            WaitNode(
                id="wait-1",
                delay=AppointmentRelativeDelay(offset_seconds=-3600),
                next_node_id="exit-1",
            ),
            ExitNode(id="exit-1"),
        ],
        entry="wait-1",
    )

    result = asyncio.run(
        dispatcher.advance(
            run,
            defn,
            context={"appointment_at": appt_at.isoformat()},
            now=_NOW,
        )
    )

    assert result.status == "waiting"
    assert sched.create_timer.await_args.kwargs["due_at"] == datetime(
        2026, 7, 3, 13, 0, 0, tzinfo=timezone.utc
    )


# ---------------------------------------------------------------------------
# advance() — drip action releases configured batches
# ---------------------------------------------------------------------------


def test_advance_drip_releases_available_batch_slot_immediately() -> None:
    session = _make_session()
    state = AutomationWorkflowDripState(
        institution_id="inst-1",
        location_id=None,
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        step_id="drip-1",
        batch_size=2,
        interval_seconds=3600,
        current_batch_number=0,
        current_batch_count=0,
        next_due_at=_NOW,
    )
    result_row = MagicMock()
    result_row.scalar_one_or_none.return_value = state
    session.execute = AsyncMock(return_value=result_row)
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            DripNode(id="drip-1", batch_size=2, interval_seconds=3600, next_node_id="exit-1"),
            ExitNode(id="exit-1", outcome="released"),
        ],
        entry="drip-1",
    )

    result = asyncio.run(dispatcher.advance(run, defn, context={}, now=_NOW))

    assert result.status == "completed"
    assert result.outcome == "released"
    assert state.current_batch_count == 1
    sched.create_timer.assert_not_awaited()
    rt.complete_step.assert_any_await(
        rt.begin_step.return_value,
        result_code="drip_released",
        result_metadata={
            "batch_number": 1,
            "batch_position": 1,
            "batch_size": 2,
            "interval_seconds": 3600,
            "release_at": _NOW.isoformat(),
        },
    )


def test_advance_drip_waits_for_next_batch_after_batch_is_full() -> None:
    session = _make_session()
    state = AutomationWorkflowDripState(
        institution_id="inst-1",
        location_id=None,
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        step_id="drip-1",
        batch_size=2,
        interval_seconds=3600,
        current_batch_number=0,
        current_batch_count=2,
        next_due_at=_NOW,
    )
    result_row = MagicMock()
    result_row.scalar_one_or_none.return_value = state
    session.execute = AsyncMock(return_value=result_row)
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)
    due_at = _NOW + timedelta(seconds=3600)

    run = _make_run()
    defn = _definition(
        nodes=[
            DripNode(id="drip-1", batch_size=2, interval_seconds=3600, next_node_id="exit-1"),
            ExitNode(id="exit-1", outcome="released"),
        ],
        entry="drip-1",
    )

    result = asyncio.run(dispatcher.advance(run, defn, context={}, now=_NOW))

    assert result.status == "waiting"
    assert result.timer_id == "timer-1"
    assert state.current_batch_number == 1
    assert state.current_batch_count == 1
    assert state.next_due_at == due_at
    sched.create_timer.assert_awaited_once()
    assert sched.create_timer.await_args.kwargs["due_at"] == due_at
    rt.wait_run.assert_awaited_once()


def test_resume_after_timer_moves_past_drip_gate() -> None:
    session = _make_session()
    waiting_step = _make_step(step_id="drip-1", step_type="drip")
    waiting_step.id = "step-exec-1"
    result_row = MagicMock()
    result_row.scalar_one_or_none.return_value = waiting_step
    session.execute = AsyncMock(return_value=result_row)
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run(status=AutomationRunStatus.WAITING.value)
    run.id = "run-1"
    run.current_step_id = "drip-1"
    defn = _definition(
        nodes=[
            DripNode(id="drip-1", batch_size=2, interval_seconds=3600, next_node_id="exit-1"),
            ExitNode(id="exit-1", outcome="released"),
        ],
        entry="drip-1",
    )

    result = asyncio.run(dispatcher.resume_after_timer(run, defn, context={}, now=_NOW))

    assert result.status == "completed"
    assert result.outcome == "released"
    assert run.current_step_id == "exit-1"
    assert waiting_step.result_code == "drip_released"
    rt.resume_run.assert_awaited_once_with(run, waiting_step)


def test_resume_after_timer_moves_past_sms_reply_wait() -> None:
    session = _make_session()
    waiting_step = _make_step(step_id="wait-sms-1", step_type="wait")
    waiting_step.id = "step-exec-1"
    waiting_step.result_code = "awaiting_sms_reply"
    result_row = MagicMock()
    result_row.scalar_one_or_none.return_value = waiting_step
    session.execute = AsyncMock(return_value=result_row)
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run(status=AutomationRunStatus.WAITING.value)
    run.id = "run-1"
    run.current_step_id = "wait-sms-1"
    defn = _definition(
        nodes=[
            WaitNode(
                id="wait-sms-1",
                wait_for=SmsReplyWaitConfig(response_window_seconds=1800),
                next_node_id="exit-1",
            ),
            ExitNode(id="exit-1", outcome="no_response"),
        ],
        entry="wait-sms-1",
    )

    result = asyncio.run(dispatcher.resume_after_timer(run, defn, context={}, now=_NOW))

    assert result.status == "completed"
    assert result.outcome == "no_response"
    assert run.current_step_id == "exit-1"
    assert waiting_step.result_code == "sms_reply_timeout"
    rt.resume_run.assert_awaited_once_with(run, waiting_step)


# ---------------------------------------------------------------------------
# advance() — compliance hold defers the send (never drops it, scope §8)
# ---------------------------------------------------------------------------


def test_advance_send_hold_defers_via_timer() -> None:
    """A 'hold' schedules a resume timer at retry_at and waits — it must NOT
    terminate the run (the pre-fix behavior dropped the message)."""
    from datetime import timedelta

    from src.app.services.automation.compliance_gate import GateResult

    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    resume_at = _NOW + timedelta(hours=5)
    gate = AsyncMock()
    gate.check = AsyncMock(
        return_value=GateResult(action="hold", reason="quiet_hours", retry_at=resume_at)
    )
    dispatcher = WorkflowStepDispatcher(session, rt, sched, gate=gate)

    run = _make_run()
    defn = _definition(
        nodes=[
            SendSmsNode(id="sms-1", body_template="Hi", next_node_id="exit-1"),
            ExitNode(id="exit-1", outcome="sent"),
        ],
        entry="sms-1",
    )

    result = asyncio.run(dispatcher.advance(run, defn, context={}, now=_NOW))

    assert result.status == "waiting"
    assert result.timer_id == "timer-1"
    sched.create_timer.assert_awaited_once()
    assert sched.create_timer.await_args.kwargs["due_at"] == resume_at
    rt.wait_run.assert_awaited_once()
    rt.complete_run.assert_not_awaited()  # the send is deferred, not dropped


def test_advance_revalidation_skips_send() -> None:
    """If dispatch-time revalidation returns a terminal outcome, the send is skipped
    and the run exits with that outcome (e.g. appointment cancelled)."""
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    reval = AsyncMock()
    reval.revalidate = AsyncMock(return_value="skipped_cancelled")
    dispatcher = WorkflowStepDispatcher(session, rt, sched, revalidator=reval)

    run = _make_run()
    defn = _definition(
        nodes=[
            SendSmsNode(id="sms-1", body_template="Hi", next_node_id="exit-1"),
            ExitNode(id="exit-1", outcome="sent"),
        ],
        entry="sms-1",
    )
    result = asyncio.run(dispatcher.advance(run, defn, context={}))

    assert result.status == "completed"
    assert result.outcome == "skipped_cancelled"
    reval.revalidate.assert_awaited_once()


def test_calendar_send_jitter_within_bounds() -> None:
    """Calendar sends get bounded jitter to avoid vendor stampedes."""
    from datetime import timedelta

    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched, calendar_jitter_seconds=300)

    run = _make_run()
    defn = _definition(
        nodes=[
            WaitNode(
                id="w1",
                delay=CalendarDelay(offset_days=1, time_of_day="09:00"),
                next_node_id="exit-1",
            ),
            ExitNode(id="exit-1"),
        ],
        entry="w1",
    )
    asyncio.run(dispatcher.advance(run, defn, context={}, now=_NOW))

    base = _compute_due_at(
        CalendarDelay(offset_days=1, time_of_day="09:00"), "UTC", _NOW
    )
    due = sched.create_timer.await_args.kwargs["due_at"]
    assert base <= due <= base + timedelta(seconds=300)


# ---------------------------------------------------------------------------
# advance() — condition node branches correctly
# ---------------------------------------------------------------------------


def test_advance_condition_true_branch() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            ConditionNode(
                id="cond-1",
                rules=[ConditionRule(field="status", op="eq", value="confirmed")],
                true_next_node_id="exit-ok",
                false_next_node_id="exit-no",
            ),
            ExitNode(id="exit-ok", outcome="confirmed"),
            ExitNode(id="exit-no", outcome="no_response"),
        ],
        entry="cond-1",
    )

    result = asyncio.run(
        dispatcher.advance(run, defn, context={"status": "confirmed"})
    )

    assert result.status == "completed"
    assert result.outcome == "confirmed"
    rt.complete_step.assert_any_await(
        rt.begin_step.return_value,
        result_code="branch_true",
        result_metadata={"branch": "true", "next_node_id": "exit-ok"},
    )


def test_advance_condition_false_branch() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    defn = _definition(
        nodes=[
            ConditionNode(
                id="cond-1",
                rules=[ConditionRule(field="status", op="eq", value="confirmed")],
                true_next_node_id="exit-ok",
                false_next_node_id="exit-no",
            ),
            ExitNode(id="exit-ok", outcome="confirmed"),
            ExitNode(id="exit-no", outcome="no_response"),
        ],
        entry="cond-1",
    )

    result = asyncio.run(
        dispatcher.advance(run, defn, context={"status": "pending"})
    )

    assert result.status == "completed"
    assert result.outcome == "no_response"
    rt.complete_step.assert_any_await(
        rt.begin_step.return_value,
        result_code="branch_false",
        result_metadata={"branch": "false", "next_node_id": "exit-no"},
    )


def test_update_patient_status_node_records_event_and_continues() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    run.id = "run-1"
    run.contact_id = "contact-1"
    defn = _definition(
        nodes=[
            UpdatePatientStatusNode(
                id="status-1",
                status="appointment_confirmed",
                note_template="Outcome {{call_outcome}}",
                next_node_id="exit-1",
            ),
            ExitNode(id="exit-1", outcome="done"),
        ],
        entry="status-1",
    )

    result = asyncio.run(
        dispatcher.advance(run, defn, context={"call_outcome": "confirmed"})
    )

    assert result.status == "completed"
    assert result.outcome == "done"
    added = session.add.call_args_list[0].args[0]
    assert added.status == "appointment_confirmed"
    assert added.note == "Outcome confirmed"
    assert result.patient_status_event_ids == [str(added.id)]
    rt.complete_step.assert_any_await(
        rt.begin_step.return_value,
        result_code="status_updated",
        result_metadata={"status": "appointment_confirmed"},
    )


def test_do_not_call_status_writes_dnc_suppression() -> None:
    session = _make_session()
    session.get = AsyncMock(return_value=SimpleNamespace(phone="+15551234567"))
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    run.id = "run-1"
    run.location_id = "loc-1"
    run.contact_id = "contact-1"
    node = UpdatePatientStatusNode(
        id="status-dnc",
        status="do_not_call_requested",
        next_node_id="exit-1",
    )
    compliance = AsyncMock()
    compliance.set_do_not_contact = AsyncMock()

    with patch(
        "src.app.services.sms_compliance.SmsComplianceService",
        return_value=compliance,
    ):
        asyncio.run(dispatcher._apply_status_side_effects(run, node))

    compliance.set_do_not_contact.assert_awaited_once()
    kwargs = compliance.set_do_not_contact.await_args.kwargs
    assert kwargs["institution_id"] == "inst-1"
    assert kwargs["phone"] == "+15551234567"
    assert kwargs["location_id"] == "loc-1"
    assert kwargs["contact_id"] == "contact-1"
    assert kwargs["reason"] == "workflow_do_not_call_requested"


# ---------------------------------------------------------------------------
# advance() — missing node fails the run
# ---------------------------------------------------------------------------


def test_advance_missing_node_fails_run() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)

    run = _make_run()
    run.current_step_id = "ghost-node"
    defn = _definition(
        nodes=[ExitNode(id="exit-1")],
        entry="exit-1",
    )

    result = asyncio.run(dispatcher.advance(run, defn, context={}))

    assert result.status == "failed"
    rt.fail_run.assert_awaited_once()


# ---------------------------------------------------------------------------
# _evaluate_rule
# ---------------------------------------------------------------------------


def _preappointment_definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        instantiate_definition(
            TEMPLATES["surgery-pre-appointment-confirmation"],
            voice_profile_id="profile-preop",
            setup_options={
                "appointment_reasons": ["bridge prep"],
                "retry_delay_1_hours": 4,
                "retry_delay_2_hours": 7,
            },
        )
    )


def test_preappointment_no_answer_routes_to_configured_second_attempt_wait() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)
    run = _make_run()
    run.current_step_id = "attempt-1-confirmed"

    result = asyncio.run(
        dispatcher.advance(
            run,
            _preappointment_definition(),
            context={"call_outcome": "no_answer"},
            now=_NOW,
        )
    )

    assert result.status == "waiting"
    assert sched.create_timer.await_args.kwargs["due_at"] == _NOW + timedelta(hours=4)
    assert any(call.kwargs.get("step_id") == "wait-retry-1" for call in rt.begin_step.await_args_list)


def test_preappointment_callback_routes_to_patient_requested_clinic_time() -> None:
    session = _make_session()
    rt = _make_runtime()
    sched = _make_scheduler()
    dispatcher = WorkflowStepDispatcher(session, rt, sched)
    run = _make_run()
    run.current_step_id = "attempt-1-confirmed"

    result = asyncio.run(
        dispatcher.advance(
            run,
            _preappointment_definition(),
            context={
                "call_outcome": "callback_requested",
                "callback_at": "2026-07-02T15:00:00",
            },
            location_timezone="America/Toronto",
            now=_NOW,
        )
    )

    assert result.status == "waiting"
    assert sched.create_timer.await_args.kwargs["due_at"] == datetime(
        2026, 7, 2, 19, 0, tzinfo=timezone.utc
    )
    assert any(call.kwargs.get("step_id") == "wait-callback-1" for call in rt.begin_step.await_args_list)


@pytest.mark.parametrize("op,field_val,rule_val,expected", [
    ("eq", "confirmed", "confirmed", True),
    ("eq", "confirmed", "pending", False),
    ("neq", "confirmed", "pending", True),
    ("in", "confirmed", ["confirmed", "pending"], True),
    ("in", "other", ["confirmed", "pending"], False),
    ("in_case_insensitive", "Bridge Prep", ["bridge prep", "implant surgery"], True),
    ("in_case_insensitive", "Bridge Prep Follow-up", ["bridge prep"], False),
    ("not_in", "other", ["confirmed"], True),
    ("is_null", None, None, True),
    ("is_null", "x", None, False),
    ("is_not_null", "x", None, True),
    ("is_not_null", None, None, False),
    ("contains", "Implant Surgery", "surgery", True),
    ("contains", "Cleaning", "surgery", False),
    ("not_contains", "Cleaning", "surgery", True),
])
def test_evaluate_rule(op, field_val, rule_val, expected) -> None:
    rule = ConditionRule(field="f", op=op, value=rule_val)
    assert _evaluate_rule(rule, {"f": field_val}) is expected


def test_evaluate_condition_and_all_true() -> None:
    node = ConditionNode(
        id="c",
        logic="AND",
        rules=[
            ConditionRule(field="a", op="eq", value="x"),
            ConditionRule(field="b", op="eq", value="y"),
        ],
        true_next_node_id="t",
        false_next_node_id="f",
    )
    assert _evaluate_condition(node, {"a": "x", "b": "y"}) is True


def test_evaluate_condition_and_one_false() -> None:
    node = ConditionNode(
        id="c",
        logic="AND",
        rules=[
            ConditionRule(field="a", op="eq", value="x"),
            ConditionRule(field="b", op="eq", value="y"),
        ],
        true_next_node_id="t",
        false_next_node_id="f",
    )
    assert _evaluate_condition(node, {"a": "x", "b": "z"}) is False


def test_evaluate_condition_or_one_true() -> None:
    node = ConditionNode(
        id="c",
        logic="OR",
        rules=[
            ConditionRule(field="a", op="eq", value="x"),
            ConditionRule(field="b", op="eq", value="y"),
        ],
        true_next_node_id="t",
        false_next_node_id="f",
    )
    assert _evaluate_condition(node, {"a": "x", "b": "z"}) is True


# ---------------------------------------------------------------------------
# _compute_due_at
# ---------------------------------------------------------------------------


def test_compute_due_at_duration() -> None:
    delay = DurationDelay(duration_seconds=3600)
    result = _compute_due_at(delay, "UTC", _NOW)
    from datetime import timedelta
    assert result == _NOW + timedelta(seconds=3600)


def test_compute_due_at_calendar_future_time() -> None:
    # _NOW is 2026-07-02 14:00 UTC = 2026-07-02 09:00 America/Chicago (UTC-5 in July)
    delay = CalendarDelay(offset_days=0, time_of_day="11:00")
    result = _compute_due_at(delay, "America/Chicago", _NOW)
    # 11:00 Chicago on same day = 16:00 UTC, which is after _NOW (14:00 UTC)
    assert result.hour == 16
    assert result.tzinfo is not None


def test_compute_due_at_calendar_past_time_advances_day() -> None:
    # _NOW is 14:00 UTC = 09:00 Chicago; if time_of_day is 08:00, it's in the past
    delay = CalendarDelay(offset_days=0, time_of_day="08:00")
    result = _compute_due_at(delay, "America/Chicago", _NOW)
    # Should advance to next day: 08:00 Chicago next day = 13:00 UTC next day
    assert result.date() > _NOW.date()


def test_compute_due_at_unknown_timezone_falls_back_to_utc() -> None:
    delay = CalendarDelay(offset_days=1, time_of_day="09:00")
    result = _compute_due_at(delay, "Fake/Zone", _NOW)
    assert result is not None  # doesn't raise; returns valid datetime


def test_compute_due_at_appointment_relative_past_returns_now() -> None:
    delay = AppointmentRelativeDelay(offset_seconds=-3600)
    result = _compute_due_at(
        delay,
        "UTC",
        _NOW,
        context={"appointment_at": "2026-07-02T14:30:00+00:00"},
    )
    assert result == _NOW


def test_compute_due_at_context_anchor_interprets_naive_time_in_location_timezone() -> None:
    delay = AppointmentRelativeDelay(offset_seconds=0, anchor_field="callback_at")

    result = _compute_due_at(
        delay,
        "America/Toronto",
        _NOW,
        context={"callback_at": "2026-07-02T15:00:00"},
    )

    assert result == datetime(2026, 7, 2, 19, 0, tzinfo=timezone.utc)
