"""Dispatcher execution for the PMS-neutral write-back node.

Item 1.1's acceptance criteria require the node to be exercised on NexHealth, on
GoTracker, and on an adapter lacking the confirmation mixin. The schema and
translation tests in ``test_nexhealth_workflow_parity.py`` do not prove the
dispatch path, so these drive ``advance()`` end to end with fake adapters.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.pms.base import SupportsAppointmentConfirmation
from src.app.services.automation.definition_schema import (
    AppointmentOffsetTrigger,
    ExitNode,
    UpdateAppointmentNode,
    WorkflowDefinition,
)
from src.app.services.automation.step_dispatcher import WorkflowStepDispatcher


def _ok(status: str):
    return SimpleNamespace(success=True, status=status, error=None, id=None)


class _FakeNexHealthAdapter(SupportsAppointmentConfirmation):
    """Mirrors the real adapter: declares the mixin, implements the contract."""

    source = "nexhealth"

    # Declared at class level so the ABC is satisfiable; each instance shadows it
    # with an AsyncMock so calls can be asserted.
    async def confirm_appointment(self, appointment_id: str):  # pragma: no cover
        raise NotImplementedError

    def __init__(self) -> None:
        self.confirm_appointment = AsyncMock(return_value=_ok("confirmed"))
        self.cancel_appointment = AsyncMock(return_value=_ok("cancelled"))
        self.reschedule_appointment = AsyncMock(return_value=_ok("rescheduled"))
        self.close = AsyncMock()


class _AdapterWithoutConfirmation:
    """A PMS that never implements SupportsAppointmentConfirmation."""

    source = "somepms"

    def __init__(self) -> None:
        self.cancel_appointment = AsyncMock(return_value=_ok("cancelled"))
        self.close = AsyncMock()


class _FakeGoTrackerAdapter:
    source = "gotracker"

    def __init__(self) -> None:
        self.set_appointment_confirmation = AsyncMock(return_value=_ok("status_updated"))
        self.set_appointment_status_id = AsyncMock(return_value=_ok("status_updated"))
        self.update_appointment = AsyncMock(return_value=_ok("appointment_updated"))
        self.close = AsyncMock()


def _runtime() -> AsyncMock:
    rt = AsyncMock()
    step = MagicMock()
    step.id = "step-exec-1"
    rt.begin_step = AsyncMock(return_value=step)
    rt.complete_step = AsyncMock(return_value=step)
    rt.fail_step = AsyncMock()
    rt.fail_run = AsyncMock()
    rt.complete_run = AsyncMock()
    return rt


def _session(working_set_row=None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.first.return_value = working_set_row
    session.execute = AsyncMock(return_value=result)
    return session


def _run() -> AutomationWorkflowRun:
    run = AutomationWorkflowRun(
        institution_id="inst-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        status="running",
    )
    run.id = "run-1"
    run.location_id = "loc-1"
    run.trigger_ref_type = "appointment"
    run.trigger_ref_id = "appt-77"
    return run


def _definition(operation: str, **node_kwargs) -> WorkflowDefinition:
    return WorkflowDefinition(
        trigger=AppointmentOffsetTrigger(offset_hours=-24),
        entry_node_id="write",
        nodes=[
            UpdateAppointmentNode(
                id="write", next_node_id="done", operation=operation, **node_kwargs
            ),
            ExitNode(id="done", outcome="confirmed"),
        ],
    )


def _advance(session, rt, adapter, definition, context=None, pms_type="nexhealth"):
    # The GoTracker branch resolves institution/location a second time inside the
    # delegated path, so three gets are staged.
    session.get = AsyncMock(
        side_effect=[
            SimpleNamespace(id="inst-1", pms_type=pms_type, slug="clinic"),
            SimpleNamespace(id="loc-1", slug="downtown"),
            SimpleNamespace(id="loc-1", slug="downtown"),
        ]
    )
    dispatcher = WorkflowStepDispatcher(session, rt, AsyncMock())
    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ),
        patch("src.app.services.audit.log_audit", new=AsyncMock()),
    ):
        return asyncio.run(dispatcher.advance(_run(), definition, context=context or {}))


def test_neutral_confirm_on_nexhealth_calls_the_contract() -> None:
    adapter = _FakeNexHealthAdapter()
    rt = _runtime()

    result = _advance(_session(), rt, adapter, _definition("confirm"))

    adapter.confirm_appointment.assert_awaited_once_with("appt-77")
    assert result.status == "completed"
    rt.fail_run.assert_not_awaited()


def test_neutral_cancel_on_nexhealth_calls_the_contract() -> None:
    adapter = _FakeNexHealthAdapter()

    result = _advance(_session(), _runtime(), adapter, _definition("cancel"))

    adapter.cancel_appointment.assert_awaited_once_with("appt-77")
    assert result.status == "completed"


def test_neutral_reschedule_on_nexhealth_builds_a_booking_from_the_projection() -> None:
    adapter = _FakeNexHealthAdapter()
    row = SimpleNamespace(
        nexhealth_patient_id="pat-5",
        provider_id="prov-9",
        appointment_type_id="type-3",
    )

    result = _advance(
        _session(working_set_row=row),
        _runtime(),
        adapter,
        _definition("reschedule", start_time="{{reschedule_start_time}}"),
        context={"reschedule_start_time": "2026-09-02T14:30:00+00:00"},
    )

    assert result.status == "completed"
    adapter.reschedule_appointment.assert_awaited_once()
    old_id, booking = adapter.reschedule_appointment.await_args.args
    assert old_id == "appt-77"
    assert booking.patient_id == "pat-5"
    assert booking.provider_id == "prov-9"
    assert booking.slot_start.startswith("2026-09-02T14:30")


def test_reschedule_without_a_projection_row_fails_loudly() -> None:
    """No appointment row means we cannot build a booking, so never guess."""
    rt = _runtime()

    result = _advance(
        _session(working_set_row=None),
        rt,
        _FakeNexHealthAdapter(),
        _definition("reschedule", start_time="{{reschedule_start_time}}"),
        context={"reschedule_start_time": "2026-09-02T14:30:00+00:00"},
    )

    assert result.status == "failed"
    assert (
        rt.fail_step.await_args.kwargs["result_code"]
        == "appointment_reschedule_unresolvable"
    )


def test_adapter_without_confirmation_mixin_fails_and_never_reports_success() -> None:
    """The core guarantee: no silent success when the PMS was never touched."""
    adapter = _AdapterWithoutConfirmation()
    rt = _runtime()

    result = _advance(_session(), rt, adapter, _definition("confirm"))

    assert result.status == "failed"
    assert (
        rt.fail_step.await_args.kwargs["result_code"]
        == "appointment_confirmation_unsupported"
    )
    rt.complete_step.assert_not_awaited()
    rt.fail_run.assert_awaited()


def test_failed_pms_call_fails_the_run() -> None:
    adapter = _FakeNexHealthAdapter()
    adapter.confirm_appointment = AsyncMock(
        return_value=SimpleNamespace(
            success=False, status="error", error="NexHealth rejected it", id=None
        )
    )
    rt = _runtime()

    result = _advance(_session(), rt, adapter, _definition("confirm"))

    assert result.status == "failed"
    assert rt.fail_step.await_args.kwargs["result_code"] == "appointment_writeback_failed"
    rt.complete_step.assert_not_awaited()


def test_neutral_confirm_on_gotracker_uses_the_existing_writeback_path() -> None:
    """GoTracker must keep its own path, locking and audit trail included."""
    adapter = _FakeGoTrackerAdapter()
    writeback_svc = MagicMock()
    writeback_svc.acquire_appointment_lock = AsyncMock()
    writeback_svc.pending_for_appointment = AsyncMock(return_value=None)
    writeback_svc.record_request = AsyncMock()

    with patch(
        "src.app.services.automation.gotracker_writeback_service."
        "GoTrackerAppointmentWritebackService",
        return_value=writeback_svc,
    ):
        result = _advance(
            _session(),
            _runtime(),
            adapter,
            _definition("confirm"),
            pms_type="gotracker",
        )

    assert result.status == "completed"
    # Routed through the GoTracker confirmation write, not the neutral contract.
    adapter.set_appointment_confirmation.assert_awaited_once()
    writeback_svc.acquire_appointment_lock.assert_awaited_once()
