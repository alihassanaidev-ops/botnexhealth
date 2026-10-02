"""Quiet-hours / permitted-send-window evaluator (Plan 01 §Services, Plan 12 §8).

Shared policy evaluator for all outbound channels. Answers two questions in the
location's local timezone (DST-aware via zoneinfo):

  * ``is_quiet_hours(location_id, now)`` — is *now* outside the permitted window?
  * ``next_permitted_window(location_id, now)`` — the next UTC instant at which a
    send would be permitted (used to defer a held send instead of dropping it).

Windows come from ``LocationOperatingHours`` (one row per ISO day-of-week,
0=Mon … 6=Sun). Semantics:
  * no row for the day  → unconfigured → no restriction (permitted all day);
  * ``is_open == False`` → closed all day (never permitted);
  * ``open_time`` / ``close_time`` bound the permitted window; a missing bound is
    treated as midnight / end-of-day respectively.

Since Item 20 a ``QuietHoursException`` may override the weekly hours for a
date, a patient, a content class, or any combination. Exactly one exception
applies — the most specific — and it replaces that day's window entirely rather
than intersecting with it. See the model for why.

Two behaviours are load-bearing and must survive any change here: a send that
becomes due inside quiet hours is **held until the window opens**, never dropped
and never sent early; and a location with no permitted window at all is
**blocked** rather than let through. ``next_permitted_window`` returning None is
what the compliance gate turns into a block, so widening this evaluator's
"permitted" answers quietly weakens both.
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.institution_location import InstitutionLocation
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.models.quiet_hours_exception import QuietHoursException

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


class _Window:
    """A resolved permitted window for one local date.

    ``open_at is None`` means no contact is permitted that day at all, which is
    how both "the clinic is closed" and "a holiday exception blocks it" arrive
    at the same place.
    """

    __slots__ = ("open_at", "close_at", "source")

    def __init__(
        self,
        open_at: time | None,
        close_at: time | None = None,
        *,
        source: str = "weekly_hours",
    ) -> None:
        self.open_at = open_at
        self.close_at = close_at
        self.source = source

    @property
    def blocked(self) -> bool:
        return self.open_at is None


#: No row anywhere → the feature is unconfigured, and an unconfigured location
#: is unrestricted. Distinct from a configured location that is closed.
_UNRESTRICTED = _Window(_DAY_START, _DAY_END, source="unconfigured")
_BLOCKED = _Window(None, source="closed")

#: Tie-break floor for rows written before created_at was populated.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _applies(
    row: QuietHoursException,
    day: date_type,
    contact_id: str | None,
    content_class: str | None,
) -> bool:
    """Whether one exception targets this send.

    NULL in a targeting column means "applies regardless". A row naming a
    patient or a content class the caller has not asked about does not apply —
    which is what stops one patient's evening-only rule quietly narrowing the
    window for everybody else.
    """
    if row.exception_date is not None and row.exception_date != day:
        return False
    if row.contact_id is not None and str(row.contact_id) != str(contact_id):
        return False
    if row.content_class is not None and row.content_class != content_class:
        return False
    return True


class QuietHoursService:
    """Timezone-aware permitted-send-window evaluator."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_quiet_hours(
        self,
        location_id: str,
        *,
        now: datetime | None = None,
        contact_id: str | None = None,
        content_class: str | None = None,
    ) -> bool:
        """True if *now* falls outside the permitted window."""
        location = await self.session.get(InstitutionLocation, location_id)
        if location is None:
            return False
        tz = _safe_zone(location.timezone)
        now_local = (now or datetime.now(tz=timezone.utc)).astimezone(tz)

        window = await self._window_for(
            location_id,
            now_local.date(),
            contact_id=contact_id,
            content_class=content_class,
        )
        if window.source == "unconfigured":
            return False
        if window.blocked:
            return True
        if window.open_at and now_local.time() < window.open_at:
            return True
        if window.close_at and now_local.time() > window.close_at:
            return True
        return False

    async def next_permitted_window(
        self,
        location_id: str,
        *,
        now: datetime | None = None,
        contact_id: str | None = None,
        content_class: str | None = None,
    ) -> datetime | None:
        """Next UTC instant a send is permitted, or None if none within the
        horizon (the caller blocks rather than deferring indefinitely)."""
        now = now or datetime.now(tz=timezone.utc)
        location = await self.session.get(InstitutionLocation, location_id)
        if location is None:
            return now  # unconfigured → permitted now
        tz = _safe_zone(location.timezone)
        now_local = now.astimezone(tz)

        for offset in range(_HORIZON_DAYS + 1):
            day_date = now_local.date() + timedelta(days=offset)
            window = await self._window_for(
                location_id,
                day_date,
                contact_id=contact_id,
                content_class=content_class,
            )

            if window.source == "unconfigured":
                start_local = datetime.combine(day_date, _DAY_START, tzinfo=tz)
                candidate = max(start_local, now_local) if offset == 0 else start_local
                return candidate.astimezone(timezone.utc)

            if window.blocked:
                continue

            open_t = window.open_at or _DAY_START
            close_t = window.close_at or _DAY_END
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

    # ── window resolution ───────────────────────────────────────────────

    async def _window_for(
        self,
        location_id: str,
        day: date_type,
        *,
        contact_id: str | None = None,
        content_class: str | None = None,
    ) -> _Window:
        """The permitted window for one local date: exception, else weekly."""
        exception = await self._matching_exception(
            location_id, day, contact_id=contact_id, content_class=content_class
        )
        if exception is not None:
            if exception.is_blocked:
                return _Window(None, source="exception")
            return _Window(
                exception.open_time or _DAY_START,
                exception.close_time or _DAY_END,
                source="exception",
            )

        hours = await self._hours_for_day(location_id, day.weekday())
        if hours is None:
            return _UNRESTRICTED
        if not hours.is_open:
            return _BLOCKED
        return _Window(hours.open_time or _DAY_START, hours.close_time or _DAY_END)

    async def _matching_exception(
        self,
        location_id: str,
        day: date_type,
        *,
        contact_id: str | None,
        content_class: str | None,
    ) -> QuietHoursException | None:
        """The single most specific exception in force, or None.

        The date narrows the query; patient and content class are matched here
        rather than in SQL. A NULL targeting column means "applies regardless",
        so each condition has to accept NULL *or* the value being asked about —
        expressed as three ``OR`` pairs it reads as a puzzle, and the precedence
        rule immediately below is the thing anyone reading this needs to follow.
        The row count per location is small: a few holidays and the patients who
        have asked for something specific.
        """
        result = await self.session.execute(
            select(QuietHoursException).where(
                QuietHoursException.location_id == location_id,
                or_(
                    QuietHoursException.exception_date.is_(None),
                    QuietHoursException.exception_date == day,
                ),
            )
        )
        candidates = [
            row
            for row in result.scalars().all()
            if _applies(row, day, contact_id, content_class)
        ]
        if not candidates:
            return None
        # Most specific wins; newest breaks a tie, since two rows of equal
        # specificity are a data-entry mistake whichever one is picked.
        return max(
            candidates,
            key=lambda row: (row.specificity, row.created_at or _EPOCH),
        )

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
