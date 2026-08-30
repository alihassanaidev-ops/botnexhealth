from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.models.call import CallStatus
from src.app.retell.webhooks import _nopms_alert_variables
from src.app.services.sms_notification_recipients import (
    resolve_sms_notification_recipients,
    unique_phone_numbers,
)


def test_unique_phone_numbers_normalizes_and_deduplicates() -> None:
    assert unique_phone_numbers(
        ["(416) 555-1234", "+1 416 555 1234", "not-a-phone", "+1 647 555 0000"]
    ) == ["+14165551234", "+16475550000"]


@pytest.mark.asyncio
async def test_resolve_sms_notification_recipients_returns_normalized_active_numbers() -> None:
    class _Scalars:
        def all(self):
            return [
                SimpleNamespace(phone_number="416-555-1234"),
                SimpleNamespace(phone_number="+1 416 555 1234"),
                SimpleNamespace(phone_number="+1 647 555 0000"),
            ]

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return _Result()

    assert await resolve_sms_notification_recipients(
        _Session(),
        institution_id="inst-1",
        notification_type="appointment_request",
    ) == ["+14165551234", "+16475550000"]


def test_nopms_staff_sms_variables_are_triage_only() -> None:
    """Staff alert wording moved into editable templates, so the PHI guarantee
    now lives in the variable set handed to them — a template can only
    interpolate what is offered here."""
    variables = _nopms_alert_variables(
        location_name="Olive Tree Dental",
        db_call=SimpleNamespace(
            id="call-1",
            call_status=CallStatus.NEEDS_BOOKING.value,
            call_tags="needs_booking",
            requested_availability="Tomorrow after 3 PM",
            is_new_patient=True,
            call_duration_seconds=142,
            patient_sentiment="Neutral",
        ),
    )

    assert variables["location_name"] == "Olive Tree Dental"
    assert variables["availability"] == "Tomorrow after 3 PM"
    assert variables["new_patient"] == "Yes"
    assert variables["emergency"] == "No"

    # No patient identifier is offered to any staff template.
    assert "patient_name" not in variables
    assert "date_of_birth" not in variables
    rendered = " ".join(variables.values())
    assert "Jane" not in rendered
    assert "+1" not in rendered
