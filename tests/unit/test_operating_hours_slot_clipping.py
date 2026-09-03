"""Operating hours must actually clip slots, and must not be expressible as nonsense.

Staging offered a 5:55am slot for a clinic whose hours were configured, because
the day was flagged open with no window and the filter read that as "open all
day". These cover both halves: the filter no longer guesses, and the API no
longer accepts the shape that made it guess.
"""

from __future__ import annotations

from datetime import time

import pytest
from pydantic import ValidationError

from src.app.api.routes.institution_setup import OperatingHoursEntry
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.pms.models import UniversalSlot
from src.app.services.slot_filter import filter_slots

TZ = "America/Toronto"
#: 2026-09-03 is a Thursday — weekday() == 3.
THURSDAY = 3


def _hours(**overrides) -> list[LocationOperatingHours]:
    """Seven rows: Thursday open 09:00-17:00, every other day closed."""
    rows = []
    for day in range(7):
        row = LocationOperatingHours(
            location_id="loc-1",
            day_of_week=day,
            is_open=day == THURSDAY,
            open_time=time(9, 0) if day == THURSDAY else None,
            close_time=time(17, 0) if day == THURSDAY else None,
        )
        if day == THURSDAY:
            for key, value in overrides.items():
                setattr(row, key, value)
        rows.append(row)
    return rows


def _slot(start: str, end: str) -> UniversalSlot:
    return UniversalSlot(start=start, end=end, provider_id="gt-3")


def _kept(slots, hours, breaks=()):
    return filter_slots(
        slots=list(slots), operating_hours=hours, breaks=list(breaks), timezone=TZ
    )


# ── The window is enforced ────────────────────────────────────────────


@pytest.mark.parametrize(
    "start,end,expected",
    [
        # Offset-aware, which is what both NexHealth and the GoTracker
        # Synchronizer actually return.
        ("2026-09-03T05:55:00-04:00", "2026-09-03T06:00:00-04:00", False),
        ("2026-09-03T08:59:00-04:00", "2026-09-03T09:29:00-04:00", False),
        ("2026-09-03T09:00:00-04:00", "2026-09-03T09:30:00-04:00", True),
        ("2026-09-03T16:30:00-04:00", "2026-09-03T17:00:00-04:00", True),
        # Ends after close.
        ("2026-09-03T16:45:00-04:00", "2026-09-03T17:15:00-04:00", False),
        ("2026-09-03T20:00:00-04:00", "2026-09-03T20:30:00-04:00", False),
    ],
)
def test_slots_are_clipped_to_the_configured_window(start, end, expected):
    kept = _kept([_slot(start, end)], _hours())
    assert bool(kept) is expected


def test_a_utc_slot_is_compared_in_the_clinics_own_timezone():
    """13:00Z is 09:00 in Toronto — inside the window, not before it."""
    assert _kept([_slot("2026-09-03T13:00:00Z", "2026-09-03T13:30:00Z")], _hours())


def test_a_closed_day_offers_nothing():
    # 2026-09-04 is a Friday, configured closed.
    assert not _kept(
        [_slot("2026-09-04T10:00:00-04:00", "2026-09-04T10:30:00-04:00")], _hours()
    )


# ── Open with no window is closed, not 24 hours ───────────────────────


def test_open_day_with_no_window_offers_nothing():
    """The staging defect. Previously every slot passed, including 05:55.

    Toggling a day off nulls its times; toggling it back on used to leave them
    null, and the filter read the row as open all day — silently disabling the
    only control the admin thought they had set.
    """
    hours = _hours(open_time=None, close_time=None)
    slots = [
        _slot("2026-09-03T05:55:00-04:00", "2026-09-03T06:00:00-04:00"),
        _slot("2026-09-03T12:00:00-04:00", "2026-09-03T12:30:00-04:00"),
        _slot("2026-09-03T23:30:00-04:00", "2026-09-03T23:59:00-04:00"),
    ]
    assert _kept(slots, hours) == []


@pytest.mark.parametrize("missing", ["open_time", "close_time"])
def test_half_a_window_is_still_no_window(missing):
    assert _kept(
        [_slot("2026-09-03T12:00:00-04:00", "2026-09-03T12:30:00-04:00")],
        _hours(**{missing: None}),
    ) == []


def test_an_unconfigured_location_is_not_clipped():
    """No rows means "nobody has set this up", not "closed"."""
    slots = [_slot("2026-09-03T05:55:00-04:00", "2026-09-03T06:00:00-04:00")]
    assert filter_slots(
        slots=slots, operating_hours=[], breaks=[], timezone=TZ
    ) == slots


# ── A bad end must not disable the whole check ────────────────────────


def test_an_unparseable_end_does_not_disable_the_hours_check():
    """A wall-clock ``EndTime`` used to buy the slot a free pass.

    ``filter_slots`` passes a slot through when it cannot parse it. An end of
    "20:30:00" raises, so a 20:00 slot outside opening hours was kept — start
    parsed perfectly, and the failure came from a field the check barely needs.
    """
    outside = UniversalSlot(
        start="2026-09-03T20:00:00-04:00", end="20:30:00", provider_id="gt-3"
    )
    assert _kept([outside], _hours()) == []


def test_an_unparseable_end_still_keeps_a_slot_inside_the_window():
    inside = UniversalSlot(
        start="2026-09-03T10:00:00-04:00", end="10:30:00", provider_id="gt-3"
    )
    assert len(_kept([inside], _hours())) == 1


def test_an_unparseable_start_is_still_passed_through():
    """Unchanged behaviour: a slot we cannot read at all is not silently dropped."""
    unreadable = UniversalSlot(start="09:00:00", end="09:30:00", provider_id="gt-3")
    assert _kept([unreadable], _hours()) == [unreadable]


# ── Breaks still apply ────────────────────────────────────────────────


def test_a_break_removes_slots_that_overlap_it():
    from src.app.models.location_break import LocationBreak

    lunch = LocationBreak(
        location_id="loc-1",
        name="Lunch",
        day_of_week=None,
        start_time=time(12, 0),
        end_time=time(13, 0),
    )
    slots = [
        _slot("2026-09-03T11:30:00-04:00", "2026-09-03T12:00:00-04:00"),  # touches
        _slot("2026-09-03T12:30:00-04:00", "2026-09-03T13:00:00-04:00"),  # inside
        _slot("2026-09-03T13:00:00-04:00", "2026-09-03T13:30:00-04:00"),  # touches
    ]
    kept = _kept(slots, _hours(), [lunch])
    assert [s.start for s in kept] == [
        "2026-09-03T11:30:00-04:00",
        "2026-09-03T13:00:00-04:00",
    ]


# ── The API will not store the shape that caused it ───────────────────


def test_an_open_day_must_carry_a_window():
    with pytest.raises(ValidationError) as err:
        OperatingHoursEntry(day_of_week=THURSDAY, is_open=True)
    assert "marked open but is missing" in str(err.value)


@pytest.mark.parametrize(
    "open_time,close_time",
    [("09:00", None), (None, "17:00")],
)
def test_an_open_day_needs_both_ends(open_time, close_time):
    with pytest.raises(ValidationError):
        OperatingHoursEntry(
            day_of_week=THURSDAY,
            is_open=True,
            open_time=open_time,
            close_time=close_time,
        )


def test_close_must_be_after_open():
    with pytest.raises(ValidationError) as err:
        OperatingHoursEntry(
            day_of_week=THURSDAY, is_open=True, open_time="17:00", close_time="09:00"
        )
    assert "close_time must be after open_time" in str(err.value)


def test_a_closed_day_needs_no_times():
    entry = OperatingHoursEntry(day_of_week=5, is_open=False)
    assert entry.is_open is False and entry.open_time is None
