"""Unit tests for the next_available_date hint on find_appointment_slots.

When a requested day is fully booked, the handler must relay NexHealth's
``next_available_date`` so the voice agent can jump straight to the next open
day instead of probing date-by-date. When slots exist, the hint is suppressed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.pms.models import SlotSearchResult, UniversalSlot
from src.app.retell import handlers


def _ctx(search_result: SlotSearchResult):
    adapter = MagicMock()
    adapter.find_available_slots = AsyncMock(return_value=search_result)
    return SimpleNamespace(
        institution=SimpleNamespace(id="11111111-1111-1111-1111-111111111111"),
        location=None,  # None → handler skips provider-settings DB lookup
        adapter=adapter,
    )


# find_appointment_slots is wrapped by the @audit decorator; call the inner fn.
_find_slots = handlers.find_appointment_slots.__wrapped__


@pytest.mark.asyncio
async def test_next_available_date_relayed_when_day_full(monkeypatch):
    ctx = _ctx(
        SlotSearchResult(
            slots=[],
            next_available_date="2026-08-01",
            next_available_by_provider={"nh-123": "2026-08-01"},
        )
    )

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)

    result = await _find_slots(
        {"start_date": "2026-07-20", "appointment_type_id": "nh-50"}
    )

    assert result["slots_count"] == 0
    assert result["next_available_date"] == "2026-08-01"
    assert result["next_available_by_provider"] == {"nh-123": "2026-08-01"}
    assert "2026-08-01" in result["message"]


@pytest.mark.asyncio
async def test_no_hint_when_no_availability_within_window(monkeypatch):
    ctx = _ctx(SlotSearchResult(slots=[], next_available_date=None))

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)

    result = await _find_slots(
        {"start_date": "2026-07-20", "appointment_type_id": "nh-50"}
    )

    assert result["slots_count"] == 0
    assert result["next_available_date"] is None
    assert "no upcoming openings" in result["message"].lower()


@pytest.mark.asyncio
async def test_hint_suppressed_when_slots_exist(monkeypatch):
    slot = UniversalSlot(
        start="2026-07-20T09:00:00-04:00",
        end="2026-07-20T09:30:00-04:00",
        provider_id="nh-123",
    )
    # next_available_date would be None here anyway from the PMS, but assert the
    # handler never leaks a stale hint alongside real slots.
    ctx = _ctx(
        SlotSearchResult(
            slots=[slot],
            next_available_date="2026-08-01",
            next_available_by_provider={"nh-123": "2026-08-01"},
        )
    )

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)

    result = await _find_slots(
        {"start_date": "2026-07-20", "appointment_type_id": "nh-50"}
    )

    assert result["slots_count"] == 1
    assert result["next_available_date"] is None
    assert result["next_available_by_provider"] == {}
    assert "Found 1" in result["message"]
