"""Slot filtering service — removes slots outside clinic operating hours or during breaks.

Pure-function approach: no DB access, all data passed in as arguments.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Sequence

from zoneinfo import ZoneInfo

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


def filter_slots(
    slots: list[UniversalSlot],
    operating_hours: Sequence[LocationOperatingHours],
    breaks: Sequence[LocationBreak],
    timezone: str = "UTC",
) -> list[UniversalSlot]:
    """
    Filter slots against clinic operating hours and break schedules.

    If no operating_hours rows exist, all slots pass through (backward-compatible).
    """
    if not operating_hours:
        # Feature not configured — pass everything through
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

            # 1. Check if day is configured and open
            day_hours = hours_by_day.get(day)
            if day_hours is None:
                # Day not configured — treat as closed (conservative)
                continue
            if not day_hours.is_open:
                continue

            # 2. Check slot is within operating hours
            if day_hours.open_time and day_hours.close_time:
                slot_start_time = local_start.time()
                slot_end_time = local_end.time()

                if slot_start_time < day_hours.open_time:
                    continue
                if slot_end_time > day_hours.close_time:
                    continue

            # 3. Check slot doesn't overlap any break
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
