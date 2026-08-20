"""The Retell appointment-type gate must not filter past-dated work windows.

Appointment-type links live on whichever work window the PMS attached them to,
and at a real clinic every single one sat on a past-dated row (69 of 69 for one
provider, 12 of 12 for another). Filtering past dates in this lookup empties the
allowed set, and `_validate_appointment_type_for_provider` then rejects every
booking, reschedule, and slot search for that provider.

That is exactly what happened when `ignore_past_dates` flipped to default-True
for the setup UI: the UI wanted past rows gone, this path needs them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.pms.base import SupportsAvailabilityLinking
from src.app.retell import handlers


class _Adapter(SupportsAvailabilityLinking):
    """Adapter whose typed work windows are all in the past, as in production."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_availabilities(self, **kwargs):
        self.calls.append(kwargs)
        rows = [
            {
                "id": 1,
                "specific_date": "2020-01-01",  # past
                "appointment_types": [{"id": 55, "name": "Cleaning"}],
            },
            {"id": 2, "specific_date": "2099-01-01", "appointment_types": []},
        ]
        if kwargs.get("ignore_past_dates", True):
            rows = [r for r in rows if r["specific_date"] >= "2026-08-21"]
        return rows

    async def link_availability(self, **kwargs):  # required by the ABC
        ...

    async def update_availability(self, **kwargs):
        ...


def _ctx(adapter):
    return SimpleNamespace(
        institution=SimpleNamespace(), location=SimpleNamespace(), adapter=adapter
    )


@pytest.mark.asyncio
async def test_validation_requests_past_dates():
    adapter = _Adapter()

    await handlers._validate_appointment_type_for_provider(
        _ctx(adapter), "nh-123", "nh-55"
    )

    assert adapter.calls, "expected the adapter to be consulted"
    assert adapter.calls[0]["ignore_past_dates"] is False


@pytest.mark.asyncio
async def test_type_on_a_past_window_is_still_accepted():
    """The regression: this returned an error and blocked every booking."""
    adapter = _Adapter()

    error = await handlers._validate_appointment_type_for_provider(
        _ctx(adapter), "nh-123", "nh-55"
    )

    assert error is None, error


@pytest.mark.asyncio
async def test_a_type_the_provider_does_not_offer_is_still_rejected():
    """The gate must still do its job — this is not a blanket allow."""
    adapter = _Adapter()

    error = await handlers._validate_appointment_type_for_provider(
        _ctx(adapter), "nh-123", "nh-999"
    )

    assert error == "Appointment type is not available for this provider."
