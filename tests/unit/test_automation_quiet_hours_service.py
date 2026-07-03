"""Unit tests for QuietHoursService (Plan 01 §Services, Plan 12 §8).

Covers is_quiet_hours and next_permitted_window, including the deferral windows a
compliance hold relies on. _hours_for_day is patched per test so the day-of-week
logic can be exercised without a database.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock

from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.services.automation.quiet_hours_service import QuietHoursService


def _loc(tz: str = "UTC"):
    m = MagicMock()
    m.timezone = tz
    return m


def _hours(is_open: bool = True, open_time=time(9, 0), close_time=time(17, 0)):
    h = MagicMock(spec=LocationOperatingHours)
    h.is_open = is_open
    h.open_time = open_time
    h.close_time = close_time
    return h


def _svc(location, hours_by_day: dict[int, object]) -> QuietHoursService:
    session = AsyncMock()
    session.get = AsyncMock(return_value=location)
    svc = QuietHoursService(session)

    async def _hours_for_day(location_id, day_of_week):
        return hours_by_day.get(day_of_week)

    svc._hours_for_day = AsyncMock(side_effect=_hours_for_day)  # type: ignore[method-assign]
    return svc


# Anchor date: 2026-07-06 is a Monday (weekday()==0).
_MON_0700 = datetime(2026, 7, 6, 7, 0, tzinfo=timezone.utc)
_MON_1200 = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
_MON_1800 = datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc)
_ALL_OPEN = {d: _hours(is_open=True) for d in range(7)}
_ALL_CLOSED = {d: _hours(is_open=False) for d in range(7)}


def test_is_quiet_hours_before_open() -> None:
    svc = _svc(_loc(), _ALL_OPEN)
    assert asyncio.run(svc.is_quiet_hours("loc-1", now=_MON_0700)) is True


def test_is_quiet_hours_within_window() -> None:
    svc = _svc(_loc(), _ALL_OPEN)
    assert asyncio.run(svc.is_quiet_hours("loc-1", now=_MON_1200)) is False


def test_is_quiet_hours_unconfigured_day_no_restriction() -> None:
    svc = _svc(_loc(), {})  # no rows → unconfigured
    assert asyncio.run(svc.is_quiet_hours("loc-1", now=_MON_0700)) is False


def test_next_window_before_open_same_day() -> None:
    svc = _svc(_loc(), _ALL_OPEN)
    got = asyncio.run(svc.next_permitted_window("loc-1", now=_MON_0700))
    assert got == datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)


def test_next_window_after_close_rolls_to_next_day() -> None:
    svc = _svc(_loc(), _ALL_OPEN)
    got = asyncio.run(svc.next_permitted_window("loc-1", now=_MON_1800))
    # Monday window closed → next open window is Tuesday 09:00.
    assert got == datetime(2026, 7, 7, 9, 0, tzinfo=timezone.utc)


def test_next_window_none_when_closed_all_week() -> None:
    svc = _svc(_loc(), _ALL_CLOSED)
    assert asyncio.run(svc.next_permitted_window("loc-1", now=_MON_1200)) is None


def test_next_window_unconfigured_returns_now() -> None:
    svc = _svc(_loc(), {})  # unconfigured → permitted immediately
    got = asyncio.run(svc.next_permitted_window("loc-1", now=_MON_0700))
    assert got == _MON_0700


def test_next_window_respects_location_timezone() -> None:
    # Toronto is UTC-4 in July (EDT). 12:00 UTC == 08:00 local (before 09:00 open).
    svc = _svc(_loc("America/Toronto"), _ALL_OPEN)
    got = asyncio.run(svc.next_permitted_window("loc-1", now=_MON_1200))
    # 09:00 EDT == 13:00 UTC.
    assert got == datetime(2026, 7, 6, 13, 0, tzinfo=timezone.utc)


def test_missing_location_permits_now() -> None:
    svc = _svc(None, _ALL_OPEN)
    got = asyncio.run(svc.next_permitted_window("loc-1", now=_MON_1200))
    assert got == _MON_1200
    assert asyncio.run(svc.is_quiet_hours("loc-1", now=_MON_1200)) is False
