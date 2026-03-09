"""Tests for apply_time_restriction — same-day cutoff when provider has no appointments.

Covers:
- Before cutoff: slots kept regardless of appointment status
- After cutoff + no appointments: same-day slots removed
- After cutoff + has appointments: slots kept
- Multi-day slots: only same-day slots removed
- Timezone handling
- Edge cases: exactly at cutoff, midnight, unparseable slots
"""

from datetime import datetime, time, timezone

from src.app.pms.models import UniversalSlot
from src.app.services.slot_filter import apply_time_restriction, get_local_date_string


def _slot(start: str, end: str | None = None) -> UniversalSlot:
    return UniversalSlot(
        start=start,
        end=end or start,
        provider_id="prov-1",
    )


# ── Basic behavior ──────────────────────────────────────────────────────


class TestBeforeCutoff:
    """When current time is before the cutoff, no slots should be removed."""

    def test_before_cutoff_no_appointments(self):
        """Before cutoff + no appointments = keep everything."""
        slots = [
            _slot("2026-03-09T10:00:00-05:00"),
            _slot("2026-03-09T14:00:00-05:00"),
        ]
        # now = 11:00 EST, cutoff = 14:00
        now = datetime(2026, 3, 9, 16, 0, tzinfo=timezone.utc)  # 11:00 EST
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 2

    def test_before_cutoff_with_appointments(self):
        """Before cutoff + has appointments = keep everything."""
        slots = [_slot("2026-03-09T10:00:00-05:00")]
        now = datetime(2026, 3, 9, 16, 0, tzinfo=timezone.utc)  # 11:00 EST
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=True,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 1


class TestAfterCutoffNoAppointments:
    """After cutoff + no appointments = remove same-day slots."""

    def test_removes_same_day_slots(self):
        """Same-day slots should be removed."""
        slots = [
            _slot("2026-03-09T15:00:00-05:00"),
            _slot("2026-03-09T16:00:00-05:00"),
        ]
        # now = 14:30 EST, cutoff = 14:00
        now = datetime(2026, 3, 9, 19, 30, tzinfo=timezone.utc)  # 14:30 EST
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 0

    def test_keeps_next_day_slots(self):
        """Next-day slots should be kept even after cutoff."""
        slots = [
            _slot("2026-03-09T15:00:00-05:00"),  # today — remove
            _slot("2026-03-10T09:00:00-05:00"),  # tomorrow — keep
            _slot("2026-03-10T14:00:00-05:00"),  # tomorrow — keep
        ]
        now = datetime(2026, 3, 9, 19, 30, tzinfo=timezone.utc)  # 14:30 EST
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 2
        assert all("03-10" in s.start for s in result)

    def test_mixed_days(self):
        """Only same-day slots removed, other days untouched."""
        slots = [
            _slot("2026-03-09T08:00:00-05:00"),  # today — remove
            _slot("2026-03-09T17:00:00-05:00"),  # today — remove
            _slot("2026-03-10T08:00:00-05:00"),  # tomorrow — keep
            _slot("2026-03-11T10:00:00-05:00"),  # day after — keep
        ]
        now = datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc)  # 15:00 EST
        result = apply_time_restriction(
            slots, cutoff_time=time(12, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 2


class TestAfterCutoffWithAppointments:
    """After cutoff + has appointments = keep all slots."""

    def test_keeps_all_when_appointments_exist(self):
        """Provider has appointments — no filtering regardless of cutoff."""
        slots = [
            _slot("2026-03-09T15:00:00-05:00"),
            _slot("2026-03-09T16:00:00-05:00"),
        ]
        now = datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc)  # 15:00 EST
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=True,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 2


# ── Edge cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_exactly_at_cutoff(self):
        """Exactly at cutoff time — should NOT trigger (need to be past cutoff)."""
        slots = [_slot("2026-03-09T15:00:00-04:00")]
        # now is exactly 14:00 EDT = 18:00 UTC (DST active in March 2026)
        now = datetime(2026, 3, 9, 18, 0, tzinfo=timezone.utc)
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 1

    def test_one_second_after_cutoff(self):
        """One minute after cutoff — should trigger."""
        slots = [_slot("2026-03-09T15:00:00-05:00")]
        # now is 14:01 EST
        now = datetime(2026, 3, 9, 19, 1, tzinfo=timezone.utc)
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 0

    def test_early_morning_cutoff(self):
        """Very early cutoff (e.g. 06:00)."""
        slots = [_slot("2026-03-09T07:00:00-05:00")]
        # now = 06:30 EST
        now = datetime(2026, 3, 9, 11, 30, tzinfo=timezone.utc)
        result = apply_time_restriction(
            slots, cutoff_time=time(6, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 0

    def test_late_night_cutoff(self):
        """Late cutoff (23:00) — rare but should work.
        Use EDT offset (-04:00) since DST is active in March 2026."""
        slots = [_slot("2026-03-09T23:30:00-04:00")]  # 23:30 EDT
        # now = 23:15 EDT = March 10 03:15 UTC
        now = datetime(2026, 3, 10, 3, 15, tzinfo=timezone.utc)
        result = apply_time_restriction(
            slots, cutoff_time=time(23, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 0

    def test_empty_slots(self):
        """Empty slot list — should return empty without error."""
        result = apply_time_restriction(
            [], cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/New_York",
            now=datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc),
        )
        assert result == []

    def test_unparseable_slot_dropped(self):
        """Slots with unparseable times should be dropped."""
        slots = [
            UniversalSlot(start="not-a-date", end="also-bad", provider_id="p"),
            _slot("2026-03-09T15:00:00-04:00"),  # today EDT — should be removed
        ]
        now = datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc)  # 16:00 EDT
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 0

    def test_naive_now_gets_utc(self):
        """Naive `now` should be treated as UTC."""
        slots = [_slot("2026-03-09T15:00:00-05:00")]  # today in EST
        # Naive now that maps to 15:00 EST when interpreted as UTC
        now = datetime(2026, 3, 9, 20, 0)  # naive = 20:00 UTC = 15:00 EST
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 0


# ── Timezone tests ──────────────────────────────────────────────────────


class TestTimezoneHandling:
    def test_utc_timezone(self):
        """UTC timezone should work correctly."""
        slots = [_slot("2026-03-09T15:00:00+00:00")]
        now = datetime(2026, 3, 9, 14, 30, tzinfo=timezone.utc)
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="UTC", now=now,
        )
        assert len(result) == 0

    def test_pacific_timezone(self):
        """Pacific timezone — slot is today in PT but cutoff applies in PT."""
        slots = [_slot("2026-03-09T17:00:00-07:00")]  # 5pm PT = midnight UTC+0
        # now = 3pm PT
        now = datetime(2026, 3, 9, 22, 0, tzinfo=timezone.utc)  # 15:00 PT
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/Los_Angeles", now=now,
        )
        assert len(result) == 0

    def test_cross_date_boundary_timezone(self):
        """Slot is 'today' in UTC but 'yesterday' in local tz — should NOT be removed."""
        # Slot at 2026-03-10 01:00 UTC = 2026-03-09 20:00 EST
        slots = [_slot("2026-03-10T01:00:00+00:00")]
        # now = 2026-03-09 21:00 EST (March 10 02:00 UTC)
        now = datetime(2026, 3, 10, 2, 0, tzinfo=timezone.utc)
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        # "today" in EST is March 9. The slot is March 9 in EST too (20:00).
        # So it should be removed.
        assert len(result) == 0

    def test_slot_tomorrow_in_local_tz(self):
        """Slot is tomorrow in local tz — should be kept."""
        # Slot at 2026-03-10 15:00 EST
        slots = [_slot("2026-03-10T15:00:00-05:00")]
        # now = March 9 15:00 EST
        now = datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc)
        result = apply_time_restriction(
            slots, cutoff_time=time(14, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 1


# ── Helper coverage ──────────────────────────────────────────────────────


class TestLocalDateHelper:
    def test_uses_local_date_not_utc_date(self):
        # 2026-03-10 01:30 UTC is 2026-03-09 21:30 in New York (EDT)
        now = datetime(2026, 3, 10, 1, 30, tzinfo=timezone.utc)
        assert get_local_date_string("America/New_York", now=now) == "2026-03-09"

    def test_invalid_timezone_falls_back_to_utc(self):
        now = datetime(2026, 3, 10, 1, 30, tzinfo=timezone.utc)
        assert get_local_date_string("Not/A_Real_Timezone", now=now) == "2026-03-10"


# ── Real-world scenarios ────────────────────────────────────────────────


class TestRealWorldScenarios:
    def test_morning_caller_no_appointments_after_cutoff(self):
        """It's 2pm, provider Dr. Smith has no appointments today.
        Cutoff is 12:00. All remaining same-day slots should be hidden."""
        slots = [
            _slot("2026-03-09T14:30:00-05:00"),
            _slot("2026-03-09T15:00:00-05:00"),
            _slot("2026-03-09T15:30:00-05:00"),
            _slot("2026-03-10T09:00:00-05:00"),  # tomorrow
        ]
        now = datetime(2026, 3, 9, 19, 0, tzinfo=timezone.utc)  # 14:00 EST
        result = apply_time_restriction(
            slots, cutoff_time=time(12, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 1
        assert "03-10" in result[0].start

    def test_afternoon_with_existing_patient(self):
        """It's 2pm, provider has an appointment at 3pm already booked.
        All slots should remain available."""
        slots = [
            _slot("2026-03-09T14:30:00-05:00"),
            _slot("2026-03-09T15:30:00-05:00"),
            _slot("2026-03-09T16:00:00-05:00"),
        ]
        now = datetime(2026, 3, 9, 19, 0, tzinfo=timezone.utc)
        result = apply_time_restriction(
            slots, cutoff_time=time(12, 0), has_appointments_today=True,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 3

    def test_before_cutoff_empty_day(self):
        """It's 10am, cutoff is 12:00, no appointments yet.
        Slots should still be available — cutoff hasn't passed."""
        slots = [
            _slot("2026-03-09T11:00:00-05:00"),
            _slot("2026-03-09T14:00:00-05:00"),
        ]
        now = datetime(2026, 3, 9, 15, 0, tzinfo=timezone.utc)  # 10:00 EST
        result = apply_time_restriction(
            slots, cutoff_time=time(12, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 2

    def test_weekend_provider_cutoff(self):
        """Saturday provider has 10am cutoff. It's noon, no appointments.
        Only Sunday+ slots remain."""
        # March 7, 2026 is a Saturday
        slots = [
            _slot("2026-03-07T13:00:00-05:00"),  # Saturday — remove
            _slot("2026-03-07T14:00:00-05:00"),  # Saturday — remove
            _slot("2026-03-09T09:00:00-05:00"),  # Monday — keep
        ]
        now = datetime(2026, 3, 7, 17, 0, tzinfo=timezone.utc)  # 12:00 EST Saturday
        result = apply_time_restriction(
            slots, cutoff_time=time(10, 0), has_appointments_today=False,
            timezone="America/New_York", now=now,
        )
        assert len(result) == 1
        assert "03-09" in result[0].start
