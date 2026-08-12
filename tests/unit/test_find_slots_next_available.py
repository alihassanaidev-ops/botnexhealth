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
    adapter.source = "gotracker"
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
async def test_gotracker_allows_slot_search_without_appointment_type(monkeypatch):
    ctx = _ctx(SlotSearchResult(slots=[], next_available_date=None))

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)

    result = await _find_slots(
        {
            "start_date": "2026-08-14",
            "days": 7,
            "provider_id": "3",
        }
    )

    assert "error" not in result
    ctx.adapter.find_available_slots.assert_awaited_once()
    assert (
        ctx.adapter.find_available_slots.await_args.kwargs["appointment_type_id"]
        is None
    )


@pytest.mark.asyncio
async def test_non_gotracker_still_requires_appointment_type(monkeypatch):
    ctx = _ctx(SlotSearchResult(slots=[], next_available_date=None))
    ctx.adapter.source = "nexhealth"

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)

    result = await _find_slots({"start_date": "2026-08-14", "provider_id": "3"})

    assert result == {"error": "appointment_type_id is required."}
    ctx.adapter.find_available_slots.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_group_for_provider_gets_clear_empty_range_message(monkeypatch):
    ctx = _ctx(SlotSearchResult(slots=[], next_available_date=None))

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)

    result = await _find_slots(
        {
            "start_date": "2026-07-20",
            "appointment_type_id": "gt-50",
            "provider_id": "gt-9",
        }
    )

    assert result["slots_count"] == 0
    assert result["next_available_date"] is None
    assert "this provider" in result["message"]
    assert "requested date range" in result["message"]


@pytest.mark.asyncio
async def test_gotracker_slots_use_clinic_tz_offset(monkeypatch):
    adapter = MagicMock()
    adapter.source = "gotracker"
    adapter.find_available_slots = AsyncMock(
        return_value=SlotSearchResult(slots=[], next_available_date=None)
    )
    ctx = SimpleNamespace(
        institution=SimpleNamespace(id="11111111-1111-1111-1111-111111111111"),
        location=SimpleNamespace(
            id="22222222-2222-2222-2222-222222222222",
            timezone="America/New_York",
        ),
        adapter=adapter,
    )

    class _FakeSession:
        async def execute(self, *_a, **_k):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: []),
                one_or_none=lambda: None,
            )

    class _FakeSessionCtx:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *_exc):
            return None

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)
    monkeypatch.setattr(
        handlers, "get_system_db_session", lambda *a, **k: _FakeSessionCtx()
    )

    await _find_slots(
        {
            "start_date": "2026-08-13",
            "appointment_type_id": "gt-50",
            "provider_id": "gt-9",
        }
    )

    adapter.find_available_slots.assert_awaited_once()
    assert adapter.find_available_slots.await_args.kwargs["tz_offset"] == "-04:00"


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
