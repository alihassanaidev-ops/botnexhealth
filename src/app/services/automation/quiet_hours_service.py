"""Quiet-hours / permitted-send-window evaluator (Plan 01 §Services, Plan 12 §8).

Shared policy evaluator for all outbound channels. Answers two questions in the
location's local timezone (DST-aware via zoneinfo):

  * ``is_quiet_hours(location_id, now)`` — is *now* outside the permitted window?
  * ``next_permitted_window(location_id, now)`` — the next UTC instant at which a
    send would be permitted (used to defer a held send instead of dropping it).

Windows are derived from ``LocationOperatingHours`` (one row per ISO day-of-week,
0=Mon … 6=Sun). Semantics:
  * no row for the day  → unconfigured → no restriction (permitted all day);
  * ``is_open == False`` → closed all day (never permitted);
  * ``open_time`` / ``close_time`` bound the permitted window; a missing bound is
    treated as midnight / end-of-day respectively.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.institution_location import InstitutionLocation
from src.app.models.location_operating_hours import LocationOperatingHours

logger = logging.getLogger(__name__)

_DAY_START = time(0, 0)
_DAY_END = time(23, 59, 59)
# How far ahead to search for a permitted window before giving up.
_HORIZON_DAYS = 7


def _safe_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, KeyError):
        logger.warning("unknown timezone '%s', falling back to UTC", name)
        return ZoneInfo("UTC")


class QuietHoursService:
    """Timezone-aware permitted-send-window evaluator."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_quiet_hours(
        self, location_id: str, *, now: datetime | None = None
    ) -> bool:
        """True if *now* falls outside the location's permitted send window."""
        location = await self.session.get(InstitutionLocation, location_id)
        if location is None:
            return False
        tz = _safe_zone(location.timezone)
        now_local = (now or datetime.now(tz=timezone.utc)).astimezone(tz)
        hours = await self._hours_for_day(location_id, now_local.weekday())
        if hours is None:
            return False  # unconfigured → no restriction
        if not hours.is_open:
            return True
        if hours.open_time and now_local.time() < hours.open_time:
            return True
        if hours.close_time and now_local.time() > hours.close_time:
            return True
        return False

    async def next_permitted_window(
        self, location_id: str, *, now: datetime | None = None
    ) -> datetime | None:
        """Return the next UTC instant a send is permitted, or None if none within
        the horizon (caller should then block rather than defer indefinitely)."""
        now = now or datetime.now(tz=timezone.utc)
        location = await self.session.get(InstitutionLocation, location_id)
        if location is None:
            return now  # unconfigured → permitted now
        tz = _safe_zone(location.timezone)
        now_local = now.astimezone(tz)

        for offset in range(_HORIZON_DAYS + 1):
            day_date = now_local.date() + timedelta(days=offset)
            hours = await self._hours_for_day(location_id, day_date.weekday())

            if hours is None:
                # Unconfigured day → permitted all day.
                start_local = datetime.combine(day_date, _DAY_START, tzinfo=tz)
                candidate = max(start_local, now_local) if offset == 0 else start_local
                return candidate.astimezone(timezone.utc)

            if not hours.is_open:
                continue

            open_t = hours.open_time or _DAY_START
            close_t = hours.close_time or _DAY_END
            window_start = datetime.combine(day_date, open_t, tzinfo=tz)
            window_close = datetime.combine(day_date, close_t, tzinfo=tz)

            if offset == 0:
                if now_local >= window_close:
                    continue  # today's window already closed
                candidate = max(window_start, now_local)
            else:
                candidate = window_start

            if candidate <= window_close:
                return candidate.astimezone(timezone.utc)

        return None

    async def _hours_for_day(
        self, location_id: str, day_of_week: int
    ) -> LocationOperatingHours | None:
        result = await self.session.execute(
            select(LocationOperatingHours)
            .where(
                LocationOperatingHours.location_id == location_id,
                LocationOperatingHours.day_of_week == day_of_week,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()
