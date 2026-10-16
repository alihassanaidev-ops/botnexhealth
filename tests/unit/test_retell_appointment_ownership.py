"""Voice cancel/confirm/reschedule only act on the verified caller's appointments.

Before this guard the agent would act on any appointment id it was handed, so a
caller could change someone else's appointment.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.pms.base import SupportsAppointmentConfirmation
from src.app.pms.models import BookingResult
from src.app.retell import caller_identity, handlers

_cancel = handlers.cancel_appointment.__wrapped__
_confirm = handlers.confirm_appointment.__wrapped__


class _Adapter(SimpleNamespace, SupportsAppointmentConfirmation):
    async def confirm_appointment(self, appointment_id):  # replaced per instance
        raise NotImplementedError


def _adapter(appointment: dict | None):
    return _Adapter(
        source="nexhealth",
        get_appointment=AsyncMock(return_value=appointment),
        cancel_appointment=AsyncMock(
            return_value=BookingResult(
                success=True, id="nh-55", source="nexhealth", status="cancelled"
            )
        ),
        confirm_appointment=AsyncMock(
            return_value=BookingResult(
                success=True, id="nh-55", source="nexhealth", status="confirmed"
            )
        ),
        reschedule_appointment=AsyncMock(
            return_value=BookingResult(
                success=True, id="nh-56", source="nexhealth", status="rescheduled"
            )
        ),
    )


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch):
    """Install a context, call id, verified patients and campaign appointment."""

    def _wire(
        adapter,
        *,
        call_id: str | None = "call-1",
        verified: set[str] = frozenset(),
        campaign_appointment: str | None = None,
    ):
        ctx = handlers.ResolvedContext(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            adapter=adapter,
        )

        async def fake_resolve():
            return ctx

        async def fake_verified(cid, patient_id):
            return cid == call_id and caller_identity.raw_pms_id(patient_id) in verified

        async def fake_campaign(*_a, **_k):
            return campaign_appointment

        monkeypatch.setattr(handlers, "_resolve_context", fake_resolve)
        monkeypatch.setattr(
            handlers,
            "get_call_context",
            lambda: {"call_id": call_id} if call_id else {},
        )
        monkeypatch.setattr(handlers, "call_verified_patient", fake_verified)
        monkeypatch.setattr(handlers, "outbound_call_appointment_id", fake_campaign)
        return ctx

    return _wire


@pytest.mark.asyncio
async def test_cancel_is_refused_for_another_patients_appointment(wire):
    adapter = _adapter({"id": 55, "patient_id": 999})
    wire(adapter, verified={"123"})

    result = await _cancel({"appointment_id": "nh-55"})

    assert result["error"] == "appointment_not_verified"
    adapter.cancel_appointment.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_goes_ahead_for_the_verified_patient(wire):
    adapter = _adapter({"id": 55, "patient_id": 123})
    wire(adapter, verified={"123"})

    result = await _cancel({"appointment_id": "nh-55"})

    assert result["success"] is True
    adapter.cancel_appointment.assert_awaited_once_with("nh-55")


@pytest.mark.asyncio
async def test_change_is_refused_without_a_call_id(wire):
    adapter = _adapter({"id": 55, "patient_id": 123})
    wire(adapter, call_id=None, verified={"123"})

    result = await _confirm({"appointment_id": "nh-55"})

    assert result["error"] == "appointment_not_verified"
    adapter.confirm_appointment.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_is_refused_when_the_appointment_cannot_be_read(wire):
    adapter = _adapter(None)
    wire(adapter, verified={"123"})

    result = await _cancel({"appointment_id": "nh-55"})

    assert result["error"] == "appointment_not_verified"
    adapter.cancel_appointment.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbound_confirmation_call_may_confirm_its_own_appointment(wire):
    """A campaign call was dialled for this appointment; no lookup is needed."""
    adapter = _adapter({"id": 55, "patient_id": 123})
    wire(adapter, campaign_appointment="55")

    result = await _confirm({"appointment_id": "nh-55"})

    assert result["success"] is True
    adapter.confirm_appointment.assert_awaited_once_with("nh-55")


@pytest.mark.asyncio
async def test_outbound_call_cannot_touch_a_different_appointment(wire):
    adapter = _adapter({"id": 77, "patient_id": 456})
    wire(adapter, campaign_appointment="55")

    result = await _cancel({"appointment_id": "nh-77"})

    assert result["error"] == "appointment_not_verified"
    adapter.cancel_appointment.assert_not_awaited()


@pytest.mark.asyncio
async def test_reschedule_must_book_the_same_patient(wire, monkeypatch):
    adapter = _adapter({"id": 55, "patient_id": 123})
    wire(adapter, verified={"123", "999"})
    monkeypatch.setattr(
        handlers, "_validate_appointment_type_for_provider", AsyncMock(return_value=None)
    )

    result = await handlers._reschedule_appointment_impl(
        {
            "old_appointment_id": "nh-55",
            "patient_id": "nh-999",
            "provider_id": "nh-1",
            "start_time": "2030-01-15T09:00:00-05:00",
            "appointment_type_id": "nh-50",
        },
        use_v2=False,
    )

    assert result["error"] == "appointment_not_verified"
    adapter.reschedule_appointment.assert_not_awaited()


def test_patient_id_is_read_from_either_pms_shape():
    assert handlers._appointment_patient_id({"patient_id": 123}) == "123"
    assert handlers._appointment_patient_id({"PatientId": "gt-9"}) == "9"
    assert handlers._appointment_patient_id({"patient": {"id": "nh-4"}}) == "4"
    assert handlers._appointment_patient_id({}) is None
