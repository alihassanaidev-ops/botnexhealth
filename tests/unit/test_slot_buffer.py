"""Tests for slot buffer time filtering.

Covers:
- apply_buffer standalone function
- filter_slots with buffer_minutes integration
- Edge cases: timezone-aware/naive datetimes, zero buffer, large buffer,
  unparseable slot times, empty slot list, exact boundary slots.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from types import SimpleNamespace

from src.app.pms.models import UniversalSlot
from src.app.services.slot_filter import apply_buffer, filter_slots, merge_buffer_minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slot(start_iso: str, end_iso: str = "", provider_id: str = "p1") -> UniversalSlot:
    """Shorthand slot builder."""
    return UniversalSlot(
        start=start_iso,
        end=end_iso or start_iso,
        provider_id=provider_id,
    )


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# Minimal stub that mimics LocationOperatingHours for filter_slots
def _hours(day_of_week: int, is_open: bool = True, open_t: str = "08:00", close_t: str = "17:00"):
    h, m = open_t.split(":")
    open_time = time(int(h), int(m))
    h, m = close_t.split(":")
    close_time = time(int(h), int(m))
    return SimpleNamespace(
        day_of_week=day_of_week,
        is_open=is_open,
        open_time=open_time,
        close_time=close_time,
    )


# ============================================================================
# apply_buffer tests
# ============================================================================


class TestMergeBufferMinutes:
    def test_enforces_provider_minimum(self):
        assert merge_buffer_minutes(requested_buffer=1, provider_buffer=60) == 60

    def test_higher_requested_buffer_wins(self):
        assert merge_buffer_minutes(requested_buffer=90, provider_buffer=60) == 90

    def test_negative_values_clamped(self):
        assert merge_buffer_minutes(requested_buffer=-10, provider_buffer=-5) == 0


class TestApplyBuffer:
    """Tests for the standalone apply_buffer function."""

    def test_zero_buffer_returns_all(self):
        """buffer_minutes=0 should return all slots unchanged."""
        slots = [
            _slot("2026-03-10T08:00:00+00:00"),
            _slot("2026-03-10T08:30:00+00:00"),
        ]
        result = apply_buffer(slots, buffer_minutes=0)
        assert len(result) == 2

    def test_negative_buffer_returns_all(self):
        """Negative buffer should be treated like zero — no filtering."""
        slots = [_slot("2026-03-10T08:00:00+00:00")]
        result = apply_buffer(slots, buffer_minutes=-10)
        assert len(result) == 1

    def test_removes_slots_before_cutoff(self):
        """Slots before now + buffer should be removed."""
        now = _utc(2026, 3, 10, 9, 0)  # 09:00 UTC
        slots = [
            _slot("2026-03-10T09:00:00+00:00"),  # exactly now — before cutoff
            _slot("2026-03-10T09:15:00+00:00"),  # 15 min from now — before cutoff
            _slot("2026-03-10T09:30:00+00:00"),  # exactly at cutoff — should PASS
            _slot("2026-03-10T10:00:00+00:00"),  # well after cutoff
        ]
        result = apply_buffer(slots, buffer_minutes=30, now=now)
        assert len(result) == 2
        assert result[0].start == "2026-03-10T09:30:00+00:00"
        assert result[1].start == "2026-03-10T10:00:00+00:00"

    def test_exact_cutoff_boundary_included(self):
        """A slot starting exactly at cutoff (now + buffer) should be included."""
        now = _utc(2026, 3, 10, 10, 0)
        slots = [_slot("2026-03-10T10:30:00+00:00")]
        result = apply_buffer(slots, buffer_minutes=30, now=now)
        assert len(result) == 1

    def test_exact_cutoff_boundary_one_second_before_excluded(self):
        """A slot 1 minute before cutoff should be excluded."""
        now = _utc(2026, 3, 10, 10, 0)
        slots = [_slot("2026-03-10T10:29:00+00:00")]
        result = apply_buffer(slots, buffer_minutes=30, now=now)
        assert len(result) == 0

    def test_large_buffer_removes_all(self):
        """A buffer larger than all slot times removes everything."""
        now = _utc(2026, 3, 10, 8, 0)
        slots = [
            _slot("2026-03-10T08:30:00+00:00"),
            _slot("2026-03-10T09:00:00+00:00"),
        ]
        result = apply_buffer(slots, buffer_minutes=1440, now=now)  # 24 hours
        assert len(result) == 0

    def test_empty_slots_returns_empty(self):
        """Empty input returns empty output."""
        result = apply_buffer([], buffer_minutes=30)
        assert result == []

    def test_timezone_aware_slot_utc_offset(self):
        """Slots with non-UTC timezone offsets are correctly compared."""
        now = _utc(2026, 3, 10, 14, 0)  # 14:00 UTC = 09:00 EST
        slots = [
            # 09:00 EST = 14:00 UTC — exactly now, should be excluded with any buffer
            _slot("2026-03-10T09:00:00-05:00"),
            # 10:00 EST = 15:00 UTC — 1 hour from now, should pass with 30m buffer
            _slot("2026-03-10T10:00:00-05:00"),
        ]
        result = apply_buffer(slots, buffer_minutes=30, now=now)
        assert len(result) == 1
        assert result[0].start == "2026-03-10T10:00:00-05:00"

    def test_naive_now_gets_utc_assumed(self):
        """If ``now`` has no tzinfo, UTC is assumed."""
        now = datetime(2026, 3, 10, 9, 0)  # naive
        slots = [
            _slot("2026-03-10T09:00:00+00:00"),
            _slot("2026-03-10T10:00:00+00:00"),
        ]
        result = apply_buffer(slots, buffer_minutes=30, now=now)
        assert len(result) == 1
        assert result[0].start == "2026-03-10T10:00:00+00:00"

    def test_naive_slot_datetime_treated_as_utc(self):
        """Slots without timezone info are treated as UTC."""
        now = _utc(2026, 3, 10, 9, 0)
        slots = [
            _slot("2026-03-10T09:00:00"),  # naive, before cutoff
            _slot("2026-03-10T10:00:00"),  # naive, after cutoff
        ]
        result = apply_buffer(slots, buffer_minutes=30, now=now)
        assert len(result) == 1

    def test_unparseable_slot_dropped(self):
        """Slots with malformed timestamps should be dropped."""
        now = _utc(2026, 3, 10, 9, 0)
        slots = [
            _slot("not-a-date"),
            _slot("2026-03-10T10:00:00+00:00"),
        ]
        result = apply_buffer(slots, buffer_minutes=30, now=now)
        assert len(result) == 1
        assert result[0].start == "2026-03-10T10:00:00+00:00"

    def test_preserves_order(self):
        """Output order matches input order."""
        now = _utc(2026, 3, 10, 9, 0)
        slots = [
            _slot("2026-03-10T11:00:00+00:00", provider_id="p_later"),
            _slot("2026-03-10T10:00:00+00:00", provider_id="p_earlier"),
        ]
        result = apply_buffer(slots, buffer_minutes=30, now=now)
        assert len(result) == 2
        assert result[0].provider_id == "p_later"
        assert result[1].provider_id == "p_earlier"

    def test_buffer_1_minute(self):
        """Minimal buffer of 1 minute works correctly."""
        now = _utc(2026, 3, 10, 9, 0)
        slots = [
            _slot("2026-03-10T09:00:00+00:00"),  # exactly now — excluded
            _slot("2026-03-10T09:00:30+00:00"),  # 30s later — still excluded (< 1 min)
            _slot("2026-03-10T09:01:00+00:00"),  # exactly 1 min — included
        ]
        result = apply_buffer(slots, buffer_minutes=1, now=now)
        assert len(result) == 1
        assert result[0].start == "2026-03-10T09:01:00+00:00"

    def test_cross_day_buffer(self):
        """Buffer that spans midnight works correctly."""
        now = _utc(2026, 3, 10, 23, 30)  # 11:30 PM UTC
        slots = [
            _slot("2026-03-10T23:45:00+00:00"),  # 15 min from now — before 60m cutoff
            _slot("2026-03-11T00:30:00+00:00"),  # exactly at cutoff
            _slot("2026-03-11T01:00:00+00:00"),  # after cutoff
        ]
        result = apply_buffer(slots, buffer_minutes=60, now=now)
        assert len(result) == 2
        assert result[0].start == "2026-03-11T00:30:00+00:00"


# ============================================================================
# filter_slots with buffer integration
# ============================================================================

class TestFilterSlotsWithBuffer:
    """Tests for filter_slots when buffer_minutes is combined with hours/breaks."""

    def test_buffer_applied_before_hours_filter(self):
        """Buffer removes slots first, then hours filter runs on the remainder."""
        now = _utc(2026, 3, 10, 8, 0)  # Tuesday 08:00 UTC
        tuesday = 1  # Monday=0, Tuesday=1

        operating_hours = [_hours(tuesday, is_open=True, open_t="09:00", close_t="17:00")]

        slots = [
            _slot("2026-03-10T08:30:00+00:00"),  # before buffer cutoff AND before hours
            _slot("2026-03-10T09:00:00+00:00"),  # after hours open but before buffer cutoff
            _slot("2026-03-10T10:00:00+00:00"),  # after cutoff AND within hours — PASS
            _slot("2026-03-10T18:00:00+00:00"),  # after cutoff but after hours close
        ]

        result = filter_slots(
            slots=slots,
            operating_hours=operating_hours,
            breaks=[],
            timezone="UTC",
            buffer_minutes=120,  # 2 hour buffer → cutoff at 10:00
            now=now,
        )
        assert len(result) == 1
        assert result[0].start == "2026-03-10T10:00:00+00:00"

    def test_buffer_zero_with_hours(self):
        """buffer_minutes=0 should not affect hours filtering."""
        tuesday = 1
        operating_hours = [_hours(tuesday, is_open=True, open_t="09:00", close_t="12:00")]
        slots = [
            _slot("2026-03-10T08:00:00+00:00"),  # before hours
            _slot("2026-03-10T10:00:00+00:00"),  # within hours
        ]
        result = filter_slots(
            slots=slots,
            operating_hours=operating_hours,
            breaks=[],
            timezone="UTC",
            buffer_minutes=0,
        )
        assert len(result) == 1
        assert result[0].start == "2026-03-10T10:00:00+00:00"

    def test_no_operating_hours_only_buffer(self):
        """When no operating hours configured, only buffer filtering applies."""
        now = _utc(2026, 3, 10, 9, 0)
        slots = [
            _slot("2026-03-10T09:00:00+00:00"),
            _slot("2026-03-10T10:00:00+00:00"),
        ]
        result = filter_slots(
            slots=slots,
            operating_hours=[],
            breaks=[],
            timezone="UTC",
            buffer_minutes=30,
            now=now,
        )
        assert len(result) == 1
        assert result[0].start == "2026-03-10T10:00:00+00:00"

    def test_buffer_with_breaks(self):
        """Buffer + break filtering work together."""
        now = _utc(2026, 3, 10, 8, 0)  # Tuesday
        tuesday = 1

        operating_hours = [_hours(tuesday, is_open=True, open_t="08:00", close_t="17:00")]
        lunch_break = SimpleNamespace(
            day_of_week=tuesday,
            start_time=time(12, 0),
            end_time=time(13, 0),
        )

        slots = [
            _slot("2026-03-10T08:00:00+00:00"),  # before buffer cutoff
            _slot("2026-03-10T09:00:00+00:00", "2026-03-10T09:30:00+00:00"),  # PASS
            _slot("2026-03-10T12:00:00+00:00", "2026-03-10T12:30:00+00:00"),  # during break
            _slot("2026-03-10T14:00:00+00:00", "2026-03-10T14:30:00+00:00"),  # PASS
        ]

        result = filter_slots(
            slots=slots,
            operating_hours=operating_hours,
            breaks=[lunch_break],
            timezone="UTC",
            buffer_minutes=30,
            now=now,
        )
        assert len(result) == 2
        assert result[0].start == "2026-03-10T09:00:00+00:00"
        assert result[1].start == "2026-03-10T14:00:00+00:00"

    def test_all_slots_removed_by_buffer(self):
        """If buffer removes everything, hours filter gets empty list."""
        now = _utc(2026, 3, 10, 16, 0)  # 4 PM
        tuesday = 1
        operating_hours = [_hours(tuesday, is_open=True, open_t="08:00", close_t="17:00")]

        slots = [
            _slot("2026-03-10T16:00:00+00:00", "2026-03-10T16:30:00+00:00"),
            _slot("2026-03-10T16:30:00+00:00", "2026-03-10T17:00:00+00:00"),
        ]

        result = filter_slots(
            slots=slots,
            operating_hours=operating_hours,
            breaks=[],
            timezone="UTC",
            buffer_minutes=120,  # cutoff at 18:00, both slots before that but also after close
            now=now,
        )
        assert len(result) == 0


# ============================================================================
# Real-world scenario tests
# ============================================================================

class TestRealWorldScenarios:
    """Realistic dental practice scenarios."""

    def test_morning_caller_30min_buffer(self):
        """Patient calls at 8:15 AM, clinic opens at 8. 30-min buffer means
        earliest slot is 8:45 AM or later."""
        now = _utc(2026, 3, 11, 13, 15)  # 8:15 AM EST (UTC-5)
        wednesday = 2

        operating_hours = [_hours(wednesday, is_open=True, open_t="08:00", close_t="17:00")]

        # Slots in EST (UTC-5)
        slots = [
            _slot("2026-03-11T08:00:00-05:00", "2026-03-11T08:30:00-05:00"),  # 8:00 AM
            _slot("2026-03-11T08:30:00-05:00", "2026-03-11T09:00:00-05:00"),  # 8:30 AM
            _slot("2026-03-11T09:00:00-05:00", "2026-03-11T09:30:00-05:00"),  # 9:00 AM
            _slot("2026-03-11T14:00:00-05:00", "2026-03-11T14:30:00-05:00"),  # 2:00 PM
        ]

        result = filter_slots(
            slots=slots,
            operating_hours=operating_hours,
            breaks=[],
            timezone="America/New_York",
            buffer_minutes=30,
            now=now,
        )

        # 8:00 AM EST = 13:00 UTC → cutoff 13:45 UTC → 8:45 AM EST
        # 8:00 excluded, 8:30 excluded, 9:00 + 14:00 included
        assert len(result) == 2
        assert "09:00" in result[0].start
        assert "14:00" in result[1].start

    def test_end_of_day_no_slots_available(self):
        """Patient calls at 4:30 PM with 60-min buffer, clinic closes at 5.
        No slots should be available."""
        now = _utc(2026, 3, 10, 21, 30)  # 4:30 PM EST
        tuesday = 1
        operating_hours = [_hours(tuesday, is_open=True, open_t="08:00", close_t="17:00")]

        slots = [
            _slot("2026-03-10T16:30:00-05:00", "2026-03-10T17:00:00-05:00"),
            _slot("2026-03-10T17:00:00-05:00", "2026-03-10T17:30:00-05:00"),  # after close
        ]

        result = filter_slots(
            slots=slots,
            operating_hours=operating_hours,
            breaks=[],
            timezone="America/New_York",
            buffer_minutes=60,
            now=now,
        )
        assert len(result) == 0

    def test_next_day_slots_survive_buffer(self):
        """Buffer only removes same-day-too-soon slots, next-day slots survive."""
        now = _utc(2026, 3, 10, 22, 0)  # 5 PM EST Tuesday
        tuesday = 1
        wednesday = 2
        operating_hours = [
            _hours(tuesday, is_open=True, open_t="08:00", close_t="17:00"),
            _hours(wednesday, is_open=True, open_t="08:00", close_t="17:00"),
        ]

        slots = [
            _slot("2026-03-10T16:30:00-05:00", "2026-03-10T17:00:00-05:00"),  # today, excluded by buffer
            _slot("2026-03-11T09:00:00-05:00", "2026-03-11T09:30:00-05:00"),  # tomorrow, passes
        ]

        result = filter_slots(
            slots=slots,
            operating_hours=operating_hours,
            breaks=[],
            timezone="America/New_York",
            buffer_minutes=120,
            now=now,
        )
        assert len(result) == 1
        assert "2026-03-11" in result[0].start
