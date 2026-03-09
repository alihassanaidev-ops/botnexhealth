"""Slot filtering service — removes slots outside clinic operating hours,
during breaks, or within the booking buffer window.

Pure-function approach: no DB access, all data passed in as arguments.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone as dt_timezone
from typing import Sequence

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.app.models.location_break import LocationBreak
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.pms.models import UniversalSlot

logger = logging.getLogger(__name__)


def _parse_iso(dt_str: str) -> datetime:
    """Parse an ISO datetime string, tolerating various formats."""
    # NexHealth typically returns "2026-03-05T09:00:00-05:00" or similar
    return datetime.fromisoformat(dt_str)


def _time_overlaps(
    slot_start: time,
    slot_end: time,
    window_start: time,
    window_end: time,
) -> bool:
    """Check if [slot_start, slot_end) overlaps with [window_start, window_end)."""
    return slot_start < window_end and slot_end > window_start


def get_local_date_string(
    timezone: str = "UTC",
    now: datetime | None = None,
) -> str:
    """Return today's date (YYYY-MM-DD) in the requested local timezone."""
    if now is None:
        now = datetime.now(dt_timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=dt_timezone.utc)

    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        logger.warning(f"Unknown timezone '{timezone}', defaulting to UTC")
        tz = dt_timezone.utc

    return now.astimezone(tz).date().isoformat()


def merge_buffer_minutes(requested_buffer: int, provider_buffer: int) -> int:
    """Return the effective buffer, enforcing provider minimums."""
    return max(0, requested_buffer, provider_buffer)


def apply_buffer(
    slots: list[UniversalSlot],
    buffer_minutes: int,
    now: datetime | None = None,
) -> list[UniversalSlot]:
    """Remove slots that start before now + buffer_minutes.

    Args:
        slots: Raw slot list.
        buffer_minutes: Minimum lead time in minutes from ``now``.
            0 or negative means no filtering.
        now: Override for current UTC time (useful for testing).

    Returns:
        Filtered list with only slots starting at or after the cutoff.
        Slots with invalid timestamps are dropped.
    """
    if buffer_minutes <= 0:
        return slots

    if now is None:
        now = datetime.now(dt_timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=dt_timezone.utc)

    cutoff = now + timedelta(minutes=buffer_minutes)

    filtered: list[UniversalSlot] = []
    dropped_invalid = 0
    for slot in slots:
        try:
            slot_start = _parse_iso(slot.start)
            # Normalize to UTC for comparison
            if slot_start.tzinfo is None:
                slot_start = slot_start.replace(tzinfo=dt_timezone.utc)
            if slot_start >= cutoff:
                filtered.append(slot)
        except (ValueError, TypeError):
            dropped_invalid += 1
            logger.warning(f"Dropping slot with invalid start time: {slot.start!r}")

    logger.info(
        f"Buffer filter ({buffer_minutes}m): {len(slots)} input → {len(filtered)} output "
        f"({len(slots) - len(filtered)} removed, invalid_dropped={dropped_invalid}, "
        f"cutoff={cutoff.isoformat()})"
    )
    return filtered


def apply_time_restriction(
    slots: list[UniversalSlot],
    cutoff_time: time,
    has_appointments_today: bool,
    timezone: str = "UTC",
    now: datetime | None = None,
) -> list[UniversalSlot]:
    """Remove same-day slots if current time is past the cutoff and
    the provider has no existing appointments today.

    Args:
        slots: Slot list (may include multi-day slots).
        cutoff_time: Local time after which same-day slots are blocked.
        has_appointments_today: Whether the provider already has booked
            appointments today. If True, slots are NOT removed.
        timezone: Clinic timezone for determining "today" and comparing times.
        now: Override for current time (useful for testing).

    Returns:
        Filtered list — same-day slots removed only when both conditions met.
        Slots with invalid timestamps are dropped.
    """
    if has_appointments_today:
        # Provider is coming in — no restriction
        return slots

    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        logger.warning(f"Unknown timezone '{timezone}', defaulting to UTC")
        tz = dt_timezone.utc

    if now is None:
        now = datetime.now(dt_timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=dt_timezone.utc)

    local_now = now.astimezone(tz)

    if local_now.time() <= cutoff_time:
        # Not past cutoff yet — no restriction
        return slots

    today_date = local_now.date()

    filtered: list[UniversalSlot] = []
    removed = 0
    dropped_invalid = 0
    for slot in slots:
        try:
            slot_start = _parse_iso(slot.start)
            if slot_start.tzinfo is None:
                slot_start = slot_start.replace(tzinfo=dt_timezone.utc)
            local_slot = slot_start.astimezone(tz)

            if local_slot.date() == today_date:
                removed += 1
                continue
        except (ValueError, TypeError):
            dropped_invalid += 1
            logger.warning(f"Dropping slot with invalid start time: {slot.start!r}")
            continue

        filtered.append(slot)

    logger.info(
        f"Time restriction (cutoff={cutoff_time}, has_appts={has_appointments_today}): "
        f"{len(slots)} input → {len(filtered)} output "
        f"({removed} same-day slots removed, invalid_dropped={dropped_invalid})"
    )
    return filtered


def filter_slots(
    slots: list[UniversalSlot],
    operating_hours: Sequence[LocationOperatingHours],
    breaks: Sequence[LocationBreak],
    timezone: str = "UTC",
    buffer_minutes: int = 0,
    now: datetime | None = None,
) -> list[UniversalSlot]:
    """
    Filter slots against clinic operating hours, break schedules,
    and minimum booking lead-time buffer.

    If no operating_hours rows exist, only the buffer filter is applied.
    """
    # 1. Apply buffer (minimum lead-time)
    if buffer_minutes > 0:
        slots = apply_buffer(slots, buffer_minutes, now=now)

    if not operating_hours:
        # Hours not configured — return buffer-filtered slots
        return slots

    # Build lookup: day_of_week → operating hours row
    hours_by_day: dict[int, LocationOperatingHours] = {
        h.day_of_week: h for h in operating_hours
    }

    # Build lookup: day_of_week → list of breaks
    # Breaks with day_of_week=None apply every day
    breaks_by_day: dict[int | None, list[LocationBreak]] = {}
    for b in breaks:
        breaks_by_day.setdefault(b.day_of_week, []).append(b)

    tz = ZoneInfo(timezone)
    filtered: list[UniversalSlot] = []

    for slot in slots:
        try:
            slot_start_dt = _parse_iso(slot.start)
            slot_end_dt = _parse_iso(slot.end) if slot.end else slot_start_dt

            # Convert to clinic's local timezone
            local_start = slot_start_dt.astimezone(tz)
            local_end = slot_end_dt.astimezone(tz)

            # ISO weekday: Monday=0 … Sunday=6
            day = local_start.weekday()

            # 2. Check if day is configured and open
            day_hours = hours_by_day.get(day)
            if day_hours is None:
                # Day not configured — treat as closed (conservative)
                continue
            if not day_hours.is_open:
                continue

            # 3. Check slot is within operating hours
            if day_hours.open_time and day_hours.close_time:
                slot_start_time = local_start.time()
                slot_end_time = local_end.time()

                if slot_start_time < day_hours.open_time:
                    continue
                if slot_end_time > day_hours.close_time:
                    continue

            # 4. Check slot doesn't overlap any break
            slot_start_time = local_start.time()
            slot_end_time = local_end.time()

            # Get breaks for this specific day + global breaks (day_of_week=None)
            applicable_breaks = breaks_by_day.get(day, []) + breaks_by_day.get(None, [])

            overlaps_break = False
            for brk in applicable_breaks:
                if _time_overlaps(slot_start_time, slot_end_time, brk.start_time, brk.end_time):
                    overlaps_break = True
                    break

            if overlaps_break:
                continue

            filtered.append(slot)

        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse slot time, passing through: {e}")
            # If we can't parse, let it through rather than silently dropping
            filtered.append(slot)

    logger.info(
        f"Slot filter: {len(slots)} input → {len(filtered)} output "
        f"({len(slots) - len(filtered)} removed)"
    )
    return filtered
