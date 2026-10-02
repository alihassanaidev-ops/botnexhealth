"""Workflow dispatcher coverage for the campaign booking action node."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.pms.models import (
    BookingResult,
    BookingWriteStatus,
    SlotSearchResult,
    UniversalSlot,
)
from src.app.services.automation.definition_schema import (
    EventTrigger,
    ExitNode,
    BookAppointmentNode,
    WorkflowDefinition,
)
from src.app.services.automation.step_dispatcher import WorkflowStepDispatcher


def _runtime() -> AsyncMock:
    runtime = AsyncMock()
    step = MagicMock()
    step.id = "step-exec-1"
    runtime.set_trace_context = MagicMock()
    runtime.begin_step = AsyncMock(return_value=step)
    runtime.complete_step = AsyncMock(return_value=step)
    runtime.fail_step = AsyncMock()
    runtime.fail_run = AsyncMock()
    runtime.complete_run = AsyncMock()
    return runtime


def _session(*, completed_step=None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = completed_step
    # No operating-hours or break rows: an unconfigured location is not clipped.
    # Left unset, ``.all()`` returns a truthy MagicMock and the booking node
    # reads it as "hours exist but no day is open", dropping every slot.
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(
        side_effect=[
            SimpleNamespace(id="inst-1", pms_type="nexhealth", slug="clinic"),
            SimpleNamespace(id="loc-1", slug="downtown", timezone="America/New_York"),
            SimpleNamespace(id="contact-1", nexhealth_patient_id="nh-patient-1"),
        ]
    )
    return session


def _run() -> AutomationWorkflowRun:
    run = AutomationWorkflowRun(
        institution_id="inst-1",
        location_id="loc-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        contact_id="contact-1",
        status="running",
    )
    run.id = "run-1"
    run.trigger_metadata = {}
    return run


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        trigger=EventTrigger(event_keys=["appointment.reminder_due"], reminder_offset_hours=-24),
        entry_node_id="book-1",
        nodes=[
            BookAppointmentNode(
                id="book-1",
                appointment_type_id="{{appointment_type_id}}",
                provider_id="{{provider_id}}",
                start_time="{{booking_start_time}}",
                booked_next_node_id="booked",
                could_not_book_next_node_id="could-not-book",
                pending_next_node_id="pending",
            ),
            ExitNode(id="booked", outcome="booked"),
            ExitNode(id="could-not-book", outcome="could_not_book"),
            ExitNode(id="pending", outcome="pending"),
        ],
    )


class _FakeBookingAdapter:
    def __init__(
        self,
        *,
        slots: list[UniversalSlot],
        result: BookingResult | None = None,
        appointments: list[dict] | None = None,
        source: str = "nexhealth",
    ):
        self.source = source
        self.find_available_slots = AsyncMock(
            return_value=SlotSearchResult(slots=slots)
        )
        self.list_appointments = AsyncMock(return_value=appointments or [])
        self.book_appointment = AsyncMock(
            return_value=result
            or BookingResult(
                success=True,
                source="nexhealth",
                status="confirmed",
                write_status=BookingWriteStatus.CONFIRMED.value,
                id="nh-appt-1",
            )
        )
        self.close = AsyncMock()


def _slot() -> UniversalSlot:
    return UniversalSlot(
        start="2026-09-02T14:30:00-04:00",
        end="2026-09-02T15:00:00-04:00",
        provider_id="nh-provider-1",
        provider_name="Dr Smith",
        appointment_type_id="nh-type-1",
    )


def _gt_slot() -> UniversalSlot:
    return UniversalSlot(
        start="2026-11-01T09:30:00-05:00",
        end="2026-11-01T10:00:00-05:00",
        provider_id="gt-provider-1",
        provider_name="Dr Smith",
        appointment_type_id="gt-type-1",
    )


def _advance(session, runtime, adapter, context=None):
    dispatcher = WorkflowStepDispatcher(session, runtime, AsyncMock())
    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ),
        patch("src.app.services.audit.log_audit", new=AsyncMock()),
    ):
        return asyncio.run(
            dispatcher.advance(
                _run(),
                _definition(),
                context=context
                or {
                    "appointment_type_id": "nh-type-1",
                    "provider_id": "nh-provider-1",
                    "booking_start_time": "2026-09-02T14:30:00-04:00",
                },
            )
        )


def _complete_step_call(runtime, result_code: str):
    return next(
        call
        for call in runtime.complete_step.await_args_list
        if call.kwargs.get("result_code") == result_code
    )


def test_book_appointment_books_available_slot_and_follows_booked_branch() -> None:
    adapter = _FakeBookingAdapter(slots=[_slot()])
    runtime = _runtime()
    run = _run()
    dispatcher = WorkflowStepDispatcher(_session(), runtime, AsyncMock())

    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ),
        patch("src.app.services.audit.log_audit", new=AsyncMock()),
    ):
        result = asyncio.run(
            dispatcher.advance(
                run,
                _definition(),
                context={
                    "appointment_type_id": "nh-type-1",
                    "provider_id": "nh-provider-1",
                    "booking_start_time": "2026-09-02T14:30:00-04:00",
                },
            )
        )

    assert result.status == "completed"
    assert result.outcome == "booked"
    adapter.find_available_slots.assert_awaited_once()
    adapter.book_appointment.assert_awaited_once()
    booking = adapter.book_appointment.await_args.args[0]
    assert booking.patient_id == "nh-patient-1"
    assert booking.provider_id == "nh-provider-1"
    assert booking.appointment_type_id == "nh-type-1"
    assert booking.slot_start == "2026-09-02T14:30:00-04:00"
    assert booking.duration_min == 30
    assert booking.provenance["actor"] == "campaign"
    assert booking.provenance["workflow_run_id"] == "run-1"
    assert booking.provenance["step_id"] == "book-1"
    assert run.trigger_ref_type == "appointment"
    assert run.trigger_ref_id == "nh-appt-1"

    call = _complete_step_call(runtime, "booked")
    assert call.kwargs["result_metadata"]["write_status"] == "confirmed"
    assert call.kwargs["result_metadata"]["next_node_id"] == "booked"


def test_book_appointment_routes_unavailable_slot_without_writing() -> None:
    adapter = _FakeBookingAdapter(slots=[])
    runtime = _runtime()

    result = _advance(_session(), runtime, adapter)

    assert result.status == "completed"
    assert result.outcome == "could_not_book"
    adapter.book_appointment.assert_not_awaited()
    call = _complete_step_call(runtime, "could_not_book")
    assert call.kwargs["result_metadata"]["reason"] == "slot_unavailable"
    assert call.kwargs["result_metadata"]["next_node_id"] == "could-not-book"


def test_book_appointment_recovers_existing_booking_when_slot_unavailable() -> None:
    adapter = _FakeBookingAdapter(
        slots=[],
        appointments=[
            {
                "id": "appt-existing",
                "patient_id": "patient-1",
                "provider_id": "provider-1",
                "appointment_type_id": "type-1",
                "start": "2026-09-02T14:30:00-04:00",
                "end": "2026-09-02T15:00:00-04:00",
                "status": "confirmed",
            }
        ],
    )
    runtime = _runtime()
    run = _run()
    dispatcher = WorkflowStepDispatcher(_session(), runtime, AsyncMock())

    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ),
        patch("src.app.services.audit.log_audit", new=AsyncMock()),
    ):
        result = asyncio.run(
            dispatcher.advance(
                run,
                _definition(),
                context={
                    "appointment_type_id": "nh-type-1",
                    "provider_id": "nh-provider-1",
                    "booking_start_time": "2026-09-02T14:30:00-04:00",
                },
            )
        )

    assert result.status == "completed"
    assert result.outcome == "booked"
    adapter.book_appointment.assert_not_awaited()
    adapter.list_appointments.assert_awaited_once()
    assert adapter.list_appointments.await_args.kwargs["start_date"] == (
        "2026-09-02T00:00:00-04:00"
    )
    assert run.trigger_ref_id == "nh-appt-existing"
    call = _complete_step_call(runtime, "booked")
    assert call.kwargs["result_metadata"]["recovered_existing_booking"] is True
    assert call.kwargs["result_metadata"]["appointment_id"] == "nh-appt-existing"
    assert call.kwargs["result_metadata"]["next_node_id"] == "booked"


def test_book_appointment_routes_gotracker_pending_to_pending_branch() -> None:
    adapter = _FakeBookingAdapter(
        slots=[_slot()],
        result=BookingResult(
            success=True,
            source="gotracker",
            status="pending",
            write_status=BookingWriteStatus.PENDING.value,
            id="gt-pending-1",
        ),
        source="gotracker",
    )
    runtime = _runtime()
    run = _run()
    dispatcher = WorkflowStepDispatcher(_session(), runtime, AsyncMock())

    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ),
        patch("src.app.services.audit.log_audit", new=AsyncMock()),
    ):
        result = asyncio.run(
            dispatcher.advance(
                run,
                _definition(),
                context={
                    "appointment_type_id": "nh-type-1",
                    "provider_id": "nh-provider-1",
                    "booking_start_time": "2026-09-02T14:30:00-04:00",
                },
            )
        )

    assert result.status == "completed"
    assert result.outcome == "pending"
    assert run.trigger_ref_type == "appointment"
    assert run.trigger_ref_id == "gt-pending-1"
    call = _complete_step_call(runtime, "pending")
    assert call.kwargs["result_metadata"]["write_status"] == "pending"
    assert call.kwargs["result_metadata"]["next_node_id"] == "pending"


def test_gotracker_booking_slot_check_omits_tz_offset() -> None:
    adapter = _FakeBookingAdapter(
        slots=[_gt_slot()],
        source="gotracker",
    )
    runtime = _runtime()

    result = _advance(
        _session(),
        runtime,
        adapter,
        context={
            "appointment_type_id": "gt-type-1",
            "provider_id": "gt-provider-1",
            "booking_start_time": "2026-11-01T09:30:00-05:00",
        },
    )

    assert result.status == "completed"
    assert result.outcome == "booked"
    adapter.find_available_slots.assert_awaited_once()
    assert "tz_offset" not in adapter.find_available_slots.await_args.kwargs


def test_gotracker_booking_conflict_recheck_omits_tz_offset() -> None:
    adapter = _FakeBookingAdapter(
        slots=[_gt_slot()],
        result=BookingResult(
            success=False,
            source="gotracker",
            status="error",
            error="slot no longer available",
        ),
        source="gotracker",
    )
    adapter.find_available_slots.side_effect = [
        SlotSearchResult(slots=[_gt_slot()]),
        SlotSearchResult(slots=[]),
    ]
    runtime = _runtime()

    result = _advance(
        _session(),
        runtime,
        adapter,
        context={
            "appointment_type_id": "gt-type-1",
            "provider_id": "gt-provider-1",
            "booking_start_time": "2026-11-01T09:30:00-05:00",
        },
    )

    assert result.status == "completed"
    assert result.outcome == "could_not_book"
    assert adapter.find_available_slots.await_count == 2
    for call in adapter.find_available_slots.await_args_list:
        assert "tz_offset" not in call.kwargs


def test_completed_booking_step_replays_branch_without_second_write() -> None:
    completed = SimpleNamespace(
        result_code="booked",
        result_metadata={
            "appointment_id": "nh-appt-existing",
            "booked_start": "2026-09-02T14:30:00-04:00",
            "write_status": "confirmed",
        },
    )
    adapter = _FakeBookingAdapter(slots=[_slot()])
    runtime = _runtime()
    session = _session(completed_step=completed)
    run = _run()
    dispatcher = WorkflowStepDispatcher(session, runtime, AsyncMock())

    with patch(
        "src.app.pms.factory.get_adapter_for_institution_location",
        new=AsyncMock(return_value=adapter),
    ):
        result = asyncio.run(
            dispatcher.advance(
                run,
                _definition(),
                context={
                    "appointment_type_id": "nh-type-1",
                    "provider_id": "nh-provider-1",
                    "booking_start_time": "2026-09-02T14:30:00-04:00",
                },
            )
        )

    assert result.status == "completed"
    assert result.outcome == "booked"
    adapter.find_available_slots.assert_not_awaited()
    adapter.book_appointment.assert_not_awaited()
    assert run.trigger_ref_id == "nh-appt-existing"
    assert runtime.begin_step.await_args_list[0].kwargs["step_id"] == "booked"


def _session_with_hours(rows) -> AsyncMock:
    """A session whose location has *rows* as its operating hours.

    Dispatches on the statement rather than on call order — the node runs a
    completed-step lookup before the hours query, so positional stubbing hands
    the wrong result to the wrong caller.
    """
    session = _session()

    def _result(scalars_all=(), first=None):
        r = MagicMock()
        r.scalars.return_value.all.return_value = list(scalars_all)
        r.scalars.return_value.first.return_value = first
        r.one_or_none.return_value = None
        return r

    async def _execute(statement, *args, **kwargs):
        sql = str(statement).lower()
        if "location_operating_hours" in sql:
            return _result(rows)
        if "location_breaks" in sql:
            return _result([])
        return _result()

    session.execute = AsyncMock(side_effect=_execute)
    return session


def _weekday_hours(day: int, open_at, close_at) -> list:
    """Seven rows; *day* open between the given times, every other day closed."""
    from src.app.models.location_operating_hours import LocationOperatingHours

    return [
        LocationOperatingHours(
            location_id="loc-1",
            day_of_week=d,
            is_open=d == day,
            open_time=open_at if d == day else None,
            close_time=close_at if d == day else None,
        )
        for d in range(7)
    ]


def _book_at(session, start: str):
    adapter = _FakeBookingAdapter(
        slots=[
            UniversalSlot(
                start=start,
                end="2026-09-02T15:00:00-04:00",
                provider_id="nh-provider-1",
                provider_name="Dr Smith",
                appointment_type_id="nh-type-1",
            )
        ]
    )
    runtime = _runtime()
    dispatcher = WorkflowStepDispatcher(session, runtime, AsyncMock())
    with (
        patch(
            "src.app.pms.factory.get_adapter_for_institution_location",
            new=AsyncMock(return_value=adapter),
        ),
        patch("src.app.services.audit.log_audit", new=AsyncMock()),
    ):
        result = asyncio.run(
            dispatcher.advance(
                _run(),
                _definition(),
                context={
                    "appointment_type_id": "nh-type-1",
                    "provider_id": "nh-provider-1",
                    "booking_start_time": start,
                },
            )
        )
    return result, adapter


def test_the_node_will_not_book_outside_the_clinics_opening_hours() -> None:
    """A campaign writes into a real diary, so the clinic's hours bind it.

    The practice software offering a slot is not consent to use it: a clinic
    that configured itself shut at 14:30 must not have a workflow book a
    patient in then.
    """
    from datetime import time

    # 2026-09-02 is a Wednesday (weekday 2). Open 09:00-12:00 only.
    session = _session_with_hours(_weekday_hours(2, time(9, 0), time(12, 0)))

    result, adapter = _book_at(session, "2026-09-02T14:30:00-04:00")

    assert result.outcome == "could_not_book"
    adapter.book_appointment.assert_not_awaited()


def test_the_node_still_books_a_slot_inside_opening_hours() -> None:
    from datetime import time

    session = _session_with_hours(_weekday_hours(2, time(9, 0), time(17, 0)))

    result, adapter = _book_at(session, "2026-09-02T14:30:00-04:00")

    assert result.outcome == "booked"
    adapter.book_appointment.assert_awaited_once()


def test_a_location_with_no_configured_hours_is_not_blocked() -> None:
    """Adding the check must not stop clinics that never set hours up."""
    session = _session_with_hours([])

    result, adapter = _book_at(session, "2026-09-02T14:30:00-04:00")

    assert result.outcome == "booked"
    adapter.book_appointment.assert_awaited_once()
