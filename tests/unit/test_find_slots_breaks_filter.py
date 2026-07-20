"""find_appointment_slots must honor per-location breaks / operating hours.

Regression guard for the client complaint "it doesn't recognize blockouts": the
Retell handler previously applied only the buffer, never the operating-hours +
breaks filter that the dashboard /slots route uses. A configured lunch break
must hide those slots from the voice agent too.
"""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.models.location_break import LocationBreak
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.pms.models import SlotSearchResult, UniversalSlot
from src.app.retell import handlers


def _all_days_open():
    # Open every day 00:00–23:59 so operating-hours pass everything and the
    # break is the only thing that removes a slot. (filter_slots ignores breaks
    # entirely when NO operating hours are configured — see slot_filter.py.)
    return [
        LocationOperatingHours(
            location_id="22222222-2222-2222-2222-222222222222",
            day_of_week=d,
            is_open=True,
            open_time=time(0, 0),
            close_time=time(23, 59),
        )
        for d in range(7)
    ]

_find_slots = handlers.find_appointment_slots.__wrapped__

# Far-future date so real "past slot"/buffer filtering never removes them.
_DAY = "2030-01-15"  # a Tuesday; break is every-day so weekday is irrelevant
_TZ_OFFSET = "-05:00"  # America/New_York in January


class _QueryResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return list(self._items)

    def one_or_none(self):
        return self._items[0] if self._items else None


class _FakeSession:
    """Returns queued query results in call order."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, *_a, **_k):
        return self._results.pop(0) if self._results else _QueryResult([])


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_exc):
        return None


def _ctx(slots):
    adapter = MagicMock()
    adapter.find_available_slots = AsyncMock(
        return_value=SlotSearchResult(slots=slots, next_available_date=None)
    )
    return SimpleNamespace(
        institution=SimpleNamespace(id="11111111-1111-1111-1111-111111111111"),
        location=SimpleNamespace(
            id="22222222-2222-2222-2222-222222222222",
            timezone="America/New_York",
        ),
        adapter=adapter,
    )


@pytest.mark.asyncio
async def test_lunch_break_hides_slot_from_agent(monkeypatch):
    during_lunch = UniversalSlot(
        start=f"{_DAY}T12:15:00{_TZ_OFFSET}",
        end=f"{_DAY}T12:45:00{_TZ_OFFSET}",
        provider_id="nh-123",
    )
    after_lunch = UniversalSlot(
        start=f"{_DAY}T14:00:00{_TZ_OFFSET}",
        end=f"{_DAY}T14:30:00{_TZ_OFFSET}",
        provider_id="nh-123",
    )
    ctx = _ctx([during_lunch, after_lunch])

    lunch = LocationBreak(
        location_id="22222222-2222-2222-2222-222222222222",
        name="Lunch",
        day_of_week=None,  # every day
        start_time=time(12, 0),
        end_time=time(13, 0),
    )
    # No provider_id in args → provider query skipped; execute order is
    # operating_hours, then breaks. Hours must be configured for breaks to apply.
    session = _FakeSession([_QueryResult(_all_days_open()), _QueryResult([lunch])])
    monkeypatch.setattr(
        handlers, "get_system_db_session", lambda *a, **k: _FakeSessionCtx(session)
    )

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)

    result = await _find_slots(
        {"start_date": _DAY, "appointment_type_id": "nh-50"}
    )

    # The 12:15 slot overlaps the 12:00–13:00 lunch break and must be dropped;
    # the 14:00 slot survives.
    assert result["slots_count"] == 1
    starts = [s["start"] for s in result["slots"]]
    assert any("14:00:00" in s for s in starts)
    assert not any("12:15:00" in s for s in starts)


@pytest.mark.asyncio
async def test_no_breaks_keeps_all_slots(monkeypatch):
    s1 = UniversalSlot(
        start=f"{_DAY}T12:15:00{_TZ_OFFSET}",
        end=f"{_DAY}T12:45:00{_TZ_OFFSET}",
        provider_id="nh-123",
    )
    s2 = UniversalSlot(
        start=f"{_DAY}T14:00:00{_TZ_OFFSET}",
        end=f"{_DAY}T14:30:00{_TZ_OFFSET}",
        provider_id="nh-123",
    )
    ctx = _ctx([s1, s2])
    # Empty operating_hours + empty breaks → nothing filtered.
    session = _FakeSession([_QueryResult([]), _QueryResult([])])
    monkeypatch.setattr(
        handlers, "get_system_db_session", lambda *a, **k: _FakeSessionCtx(session)
    )

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)

    result = await _find_slots({"start_date": _DAY, "appointment_type_id": "nh-50"})
    assert result["slots_count"] == 2
