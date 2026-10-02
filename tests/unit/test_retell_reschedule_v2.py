from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.pms.models import BookingRequest, BookingResult
from src.app.retell import handlers


@pytest.mark.asyncio
async def test_reschedule_v2_handler_uses_adapter_v2(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = SimpleNamespace(
        source="nexhealth",
        reschedule_appointment=AsyncMock(),
        reschedule_appointment_v2=AsyncMock(
            return_value=BookingResult(
                success=True,
                id="nh-old-1",
                source="nexhealth",
                status="rescheduled",
            )
        ),
    )

    async def fake_resolve_context():
        return handlers.ResolvedContext(
            institution=SimpleNamespace(),
            location=SimpleNamespace(),
            adapter=adapter,
        )

    async def fake_validate_appointment_type(*_args, **_kwargs):
        return None

    monkeypatch.setattr(handlers, "_resolve_context", fake_resolve_context)
    monkeypatch.setattr(
        handlers,
        "_validate_appointment_type_for_provider",
        fake_validate_appointment_type,
    )

    result = await handlers._reschedule_appointment_impl(
        {
            "old_appointment_id": "nh-old-1",
            "patient_id": "nh-patient-1",
            "provider_id": "nh-provider-1",
            "appointment_type_id": "nh-type-1",
            "start_time": "2026-05-04T09:00:00Z",
            "end_time": "2026-05-04T09:30:00Z",
            "operatory_id": "nh-op-1",
            "note": "Move appointment",
        },
        use_v2=True,
    )

    assert result["success"] is True
    adapter.reschedule_appointment.assert_not_awaited()
    adapter.reschedule_appointment_v2.assert_awaited_once()
    old_id, booking = adapter.reschedule_appointment_v2.await_args.args
    assert old_id == "nh-old-1"
    assert booking == BookingRequest(
        patient_id="nh-patient-1",
        provider_id="nh-provider-1",
        appointment_type_id="nh-type-1",
        slot_start="2026-05-04T09:00:00Z",
        slot_end="2026-05-04T09:30:00Z",
        operatory_id="nh-op-1",
        note="Move appointment",
    )
