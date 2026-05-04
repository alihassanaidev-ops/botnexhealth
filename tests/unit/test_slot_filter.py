"""Unit tests for slot_filter.filter_slots()."""

from datetime import time
from dataclasses import dataclass


from src.app.pms.models import UniversalSlot
from src.app.services.slot_filter import filter_slots


# ── Lightweight stand-ins for ORM models (avoid DB dependency) ───────────


@dataclass
class FakeOperatingHours:
    location_id: str = "loc-1"
    day_of_week: int = 0
    is_open: bool = True
    open_time: time | None = None
    close_time: time | None = None


@dataclass
class FakeBreak:
    location_id: str = "loc-1"
    name: str = "Lunch"
    day_of_week: int | None = None
    start_time: time = time(12, 0)
    end_time: time = time(13, 0)


# ── Helpers ──────────────────────────────────────────────────────────────


def _slot(start: str, end: str, provider_id: str = "p1") -> UniversalSlot:
    return UniversalSlot(start=start, end=end, provider_id=provider_id)


def _weekday_hours(
    open_h: int = 8,
    close_h: int = 17,
    is_open: bool = True,
) -> list[FakeOperatingHours]:
    """Mon-Fri open, Sat-Sun closed."""
    hours = []
    for day in range(7):
        if day < 5:
            hours.append(FakeOperatingHours(
                day_of_week=day,
                is_open=is_open,
                open_time=time(open_h, 0),
                close_time=time(close_h, 0),
            ))
        else:
            hours.append(FakeOperatingHours(
                day_of_week=day,
                is_open=False,
            ))
    return hours


# ── Tests ────────────────────────────────────────────────────────────────


class TestFilterSlotsBackwardCompat:
    """When no operating hours configured, all slots pass through."""

    def test_no_hours_configured(self):
        slots = [
            _slot("2026-03-05T09:00:00-05:00", "2026-03-05T09:30:00-05:00"),
            _slot("2026-03-05T22:00:00-05:00", "2026-03-05T22:30:00-05:00"),
        ]
        result = filter_slots(slots, operating_hours=[], breaks=[], timezone="America/New_York")
        assert len(result) == 2


class TestFilterSlotsOperatingHours:
    """Slots outside open/close windows are rejected."""

    def test_slot_within_hours_passes(self):
        # 2026-03-05 is a Thursday (weekday=3)
        slots = [_slot("2026-03-05T10:00:00-05:00", "2026-03-05T10:30:00-05:00")]
        hours = _weekday_hours()
        result = filter_slots(slots, operating_hours=hours, breaks=[], timezone="America/New_York")
        assert len(result) == 1

    def test_slot_before_open_rejected(self):
        # Slot at 7:00-7:30 but clinic opens at 8:00
        slots = [_slot("2026-03-05T07:00:00-05:00", "2026-03-05T07:30:00-05:00")]
        hours = _weekday_hours(open_h=8)
        result = filter_slots(slots, operating_hours=hours, breaks=[], timezone="America/New_York")
        assert len(result) == 0

    def test_slot_after_close_rejected(self):
        # Slot at 17:30-18:00 but clinic closes at 17:00
        slots = [_slot("2026-03-05T17:30:00-05:00", "2026-03-05T18:00:00-05:00")]
        hours = _weekday_hours(close_h=17)
        result = filter_slots(slots, operating_hours=hours, breaks=[], timezone="America/New_York")
        assert len(result) == 0

    def test_slot_ending_after_close_rejected(self):
        # Slot starts at 16:45, ends at 17:15 — bleeds past close
        slots = [_slot("2026-03-05T16:45:00-05:00", "2026-03-05T17:15:00-05:00")]
        hours = _weekday_hours(close_h=17)
        result = filter_slots(slots, operating_hours=hours, breaks=[], timezone="America/New_York")
        assert len(result) == 0

    def test_closed_day_rejected(self):
        # 2026-03-07 is a Saturday (weekday=5, marked closed)
        slots = [_slot("2026-03-07T10:00:00-05:00", "2026-03-07T10:30:00-05:00")]
        hours = _weekday_hours()
        result = filter_slots(slots, operating_hours=hours, breaks=[], timezone="America/New_York")
        assert len(result) == 0


class TestFilterSlotsBreaks:
    """Slots overlapping break windows are rejected."""

    def test_slot_during_break_rejected(self):
        # Slot exactly in lunch break 12:00-13:00
        slots = [_slot("2026-03-05T12:00:00-05:00", "2026-03-05T12:30:00-05:00")]
        hours = _weekday_hours()
        breaks = [FakeBreak()]  # 12:00-13:00 every day
        result = filter_slots(slots, operating_hours=hours, breaks=breaks, timezone="America/New_York")
        assert len(result) == 0

    def test_slot_partially_overlapping_break_rejected(self):
        # Slot 11:45-12:15, overlaps with 12:00-13:00 break
        slots = [_slot("2026-03-05T11:45:00-05:00", "2026-03-05T12:15:00-05:00")]
        hours = _weekday_hours()
        breaks = [FakeBreak()]
        result = filter_slots(slots, operating_hours=hours, breaks=breaks, timezone="America/New_York")
        assert len(result) == 0

    def test_slot_before_break_passes(self):
        # Slot 11:00-11:30, break is 12:00-13:00
        slots = [_slot("2026-03-05T11:00:00-05:00", "2026-03-05T11:30:00-05:00")]
        hours = _weekday_hours()
        breaks = [FakeBreak()]
        result = filter_slots(slots, operating_hours=hours, breaks=breaks, timezone="America/New_York")
        assert len(result) == 1

    def test_slot_after_break_passes(self):
        # Slot 13:00-13:30, break is 12:00-13:00
        slots = [_slot("2026-03-05T13:00:00-05:00", "2026-03-05T13:30:00-05:00")]
        hours = _weekday_hours()
        breaks = [FakeBreak()]
        result = filter_slots(slots, operating_hours=hours, breaks=breaks, timezone="America/New_York")
        assert len(result) == 1

    def test_day_specific_break_only_applies_that_day(self):
        # Break only on Thursday (day=3), 2026-03-05 is Thursday
        thursday_break = FakeBreak(day_of_week=3)
        hours = _weekday_hours()

        # Thursday slot during break → rejected
        thu_slots = [_slot("2026-03-05T12:30:00-05:00", "2026-03-05T13:00:00-05:00")]
        assert len(filter_slots(thu_slots, hours, [thursday_break], "America/New_York")) == 0

        # Wednesday slot at same time → passes (break is Thursday-only)
        wed_slots = [_slot("2026-03-04T12:30:00-05:00", "2026-03-04T13:00:00-05:00")]
        assert len(filter_slots(wed_slots, hours, [thursday_break], "America/New_York")) == 1


class TestFilterSlotsTimezone:
    """Timezone conversion is handled correctly."""

    def test_utc_slot_converted_to_local(self):
        # Slot is 13:00 UTC = 08:00 EST. Clinic opens at 08:00 EST → should pass
        slots = [_slot("2026-03-05T13:00:00+00:00", "2026-03-05T13:30:00+00:00")]
        hours = _weekday_hours(open_h=8, close_h=17)
        result = filter_slots(slots, operating_hours=hours, breaks=[], timezone="America/New_York")
        assert len(result) == 1

    def test_utc_slot_too_early_for_local(self):
        # Slot is 12:00 UTC = 07:00 EST. Clinic opens at 08:00 EST → should be rejected
        slots = [_slot("2026-03-05T12:00:00+00:00", "2026-03-05T12:30:00+00:00")]
        hours = _weekday_hours(open_h=8, close_h=17)
        result = filter_slots(slots, operating_hours=hours, breaks=[], timezone="America/New_York")
        assert len(result) == 0


class TestFilterSlotsMixedScenarios:
    """Combined filtering: some pass, some don't."""

    def test_mixed_valid_and_invalid_slots(self):
        hours = _weekday_hours(open_h=9, close_h=17)
        breaks = [FakeBreak(start_time=time(12, 0), end_time=time(13, 0))]

        slots = [
            _slot("2026-03-05T09:00:00-05:00", "2026-03-05T09:30:00-05:00"),  # ✓ within hours
            _slot("2026-03-05T07:00:00-05:00", "2026-03-05T07:30:00-05:00"),  # ✗ before open
            _slot("2026-03-05T12:30:00-05:00", "2026-03-05T13:00:00-05:00"),  # ✗ during break
            _slot("2026-03-05T14:00:00-05:00", "2026-03-05T14:30:00-05:00"),  # ✓ after break
            _slot("2026-03-07T10:00:00-05:00", "2026-03-07T10:30:00-05:00"),  # ✗ Saturday
            _slot("2026-03-05T16:30:00-05:00", "2026-03-05T17:00:00-05:00"),  # ✓ last slot
        ]

        result = filter_slots(slots, hours, breaks, "America/New_York")
        assert len(result) == 3
        assert result[0].start == "2026-03-05T09:00:00-05:00"
        assert result[1].start == "2026-03-05T14:00:00-05:00"
        assert result[2].start == "2026-03-05T16:30:00-05:00"

    def test_unparseable_slot_passes_through(self):
        """Slots with malformed times should pass through rather than be silently dropped."""
        hours = _weekday_hours()
        slots = [
            UniversalSlot(start="not-a-date", end="also-bad", provider_id="p1"),
        ]
        result = filter_slots(slots, hours, [], "America/New_York")
        assert len(result) == 1
