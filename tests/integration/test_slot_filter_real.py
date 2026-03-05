"""
Integration tests for slot filtering using REAL NexHealth API.

These tests hit the live NexHealth sandbox API, fetch real slots
for "Relaxation Dental 2", and verify our slot_filter works
correctly against real-world data.

Run:
    .venv/bin/python -m pytest tests/integration/test_slot_filter_real.py -v -s
"""

from __future__ import annotations

import os
import logging
from datetime import time, date, datetime

import pytest
import pytest_asyncio

from src.app.config import Settings, settings
from src.app.nexhealth.client import NexHealthClient
from src.app.api.helpers import handle_nexhealth_request, fetch_all_pages
from src.app.pms.nexhealth.mappers import to_slot, to_location, to_provider
from src.app.pms.models import UniversalSlot
from src.app.services.slot_filter import filter_slots

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — target clinic
# ---------------------------------------------------------------------------
TARGET_CLINIC_NAME = "Relaxation Dental 2"

# ---------------------------------------------------------------------------
# Lightweight stand-ins for ORM models (avoid DB dependency)
# ---------------------------------------------------------------------------

class FakeOperatingHours:
    def __init__(self, day_of_week: int, is_open: bool = True,
                 open_time: time | None = None, close_time: time | None = None):
        self.location_id = "test"
        self.day_of_week = day_of_week
        self.is_open = is_open
        self.open_time = open_time
        self.close_time = close_time


class FakeBreak:
    def __init__(self, name: str = "Lunch", day_of_week: int | None = None,
                 start_time: time = time(12, 0), end_time: time = time(13, 0)):
        self.location_id = "test"
        self.name = name
        self.day_of_week = day_of_week
        self.start_time = start_time
        self.end_time = end_time


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def nh_client():
    """Real NexHealth client from .env settings."""
    async with NexHealthClient(settings) as client:
        yield client


@pytest_asyncio.fixture
async def relaxation_dental_2(nh_client):
    """
    Discover the subdomain, location_id, and a provider_id
    for "Relaxation Dental 2".
    """
    # 1. Fetch all institutions
    raw = await handle_nexhealth_request(nh_client, "GET", "/institutions", params={"per_page": 50})
    institutions = raw.get("data", [])
    assert institutions, "No institutions returned from NexHealth"

    # 2. Find "Relaxation Dental 2"
    subdomain = None
    location_id = None
    timezone = None

    for inst in institutions:
        for loc in inst.get("locations", []):
            if TARGET_CLINIC_NAME.lower() in (loc.get("name") or "").lower():
                subdomain = inst.get("subdomain")
                location_id = loc.get("id")
                timezone = loc.get("timezone") or "America/New_York"
                break
        if location_id:
            break

    if not location_id:
        pytest.skip(f"'{TARGET_CLINIC_NAME}' not found in NexHealth institutions")

    print(f"\n✓ Found '{TARGET_CLINIC_NAME}': subdomain={subdomain}, location_id={location_id}, tz={timezone}")

    # 3. Get a provider for this location
    prov_raw = await handle_nexhealth_request(
        nh_client, "GET", "/providers",
        params={"subdomain": subdomain, "location_id": location_id, "page": 1, "per_page": 10}
    )
    providers = prov_raw.get("data", [])
    provider_id = providers[0]["id"] if providers else None

    return {
        "subdomain": subdomain,
        "location_id": location_id,
        "provider_id": provider_id,
        "timezone": timezone,
        "client": nh_client,
    }


@pytest_asyncio.fixture
async def real_slots(relaxation_dental_2):
    """Fetch real slots from NexHealth for "Relaxation Dental 2"."""
    info = relaxation_dental_2
    start = date.today().isoformat()

    params = {
        "subdomain": info["subdomain"],
        "start_date": start,
        "days": 7,
        "lids[]": [info["location_id"]],
    }
    if info["provider_id"]:
        params["pids[]"] = [info["provider_id"]]

    raw = await handle_nexhealth_request(info["client"], "GET", "/appointment_slots", params=params)

    # Flatten NexHealth grouped response into UniversalSlots
    slots: list[UniversalSlot] = []
    for group in raw.get("data", []):
        group_pid = group.get("pid")
        group_lid = group.get("lid")
        for slot in group.get("slots", []):
            slot["_pid"] = group_pid
            slot["_lid"] = group_lid
            slots.append(to_slot(slot))

    print(f"✓ Fetched {len(slots)} real slots from NexHealth (next 7 days)")
    return slots, info["timezone"]


# ===========================================================================
# Tests
# ===========================================================================


class TestRealNexHealthConnection:
    """Verify we can talk to NexHealth and find the target clinic."""

    @pytest.mark.asyncio
    async def test_can_authenticate(self, nh_client):
        """NexHealth client authenticates with real API key."""
        raw = await handle_nexhealth_request(nh_client, "GET", "/institutions", params={"per_page": 1})
        assert "data" in raw, "Expected 'data' key in NexHealth response"
        print(f"✓ Authenticated. Got {len(raw['data'])} institution(s).")

    @pytest.mark.asyncio
    async def test_can_find_relaxation_dental_2(self, relaxation_dental_2):
        """We can discover 'Relaxation Dental 2' and its metadata."""
        info = relaxation_dental_2
        assert info["subdomain"], "Subdomain should not be empty"
        assert info["location_id"], "Location ID should not be empty"
        print(f"✓ Clinic found: subdomain={info['subdomain']}, loc={info['location_id']}, tz={info['timezone']}")

    @pytest.mark.asyncio
    async def test_can_fetch_providers(self, relaxation_dental_2):
        """We can fetch providers for the clinic."""
        info = relaxation_dental_2
        prov_raw = await handle_nexhealth_request(
            info["client"], "GET", "/providers",
            params={
                "subdomain": info["subdomain"],
                "location_id": info["location_id"],
                "page": 1, "per_page": 50,
            },
        )
        providers = prov_raw.get("data", [])
        assert len(providers) > 0, "Should have at least 1 provider"
        print(f"✓ Found {len(providers)} provider(s):")
        for p in providers[:5]:
            print(f"  - {p.get('name', 'N/A')} (id={p.get('id')})")

    @pytest.mark.asyncio
    async def test_can_fetch_locations(self, relaxation_dental_2):
        """We can fetch location details."""
        info = relaxation_dental_2
        loc_raw = await handle_nexhealth_request(
            info["client"], "GET", f"/locations/{info['location_id']}"
        )
        loc = loc_raw.get("data", {})
        assert loc, "Location data should not be empty"

        ul = to_location(loc, subdomain=info["subdomain"])
        print(f"✓ Location: {ul.name}, tz={ul.timezone}, addr={ul.address}")
        assert ul.name, "Location should have a name"


class TestRealSlotsFetching:
    """Verify we can fetch real slots and they have expected structure."""

    @pytest.mark.asyncio
    async def test_fetch_slots_returns_data(self, real_slots):
        """NexHealth returns slots for the next 7 days."""
        slots, tz = real_slots
        # Slots COULD be empty if no availability, but the API call should succeed
        print(f"✓ {len(slots)} slots fetched. Timezone: {tz}")
        if slots:
            print(f"  First slot: {slots[0].start} → {slots[0].end}")
            print(f"  Last slot:  {slots[-1].start} → {slots[-1].end}")

    @pytest.mark.asyncio
    async def test_slots_have_valid_structure(self, real_slots):
        """Every slot has start, end, and provider_id."""
        slots, _ = real_slots
        if not slots:
            pytest.skip("No slots returned — provider may have no availability")

        for slot in slots:
            assert slot.start, f"Slot missing start: {slot}"
            assert slot.end, f"Slot missing end: {slot}"
            assert slot.provider_id, f"Slot missing provider_id: {slot}"
            # Verify ISO parseable
            datetime.fromisoformat(slot.start)
            datetime.fromisoformat(slot.end)

        print(f"✓ All {len(slots)} slots have valid start/end/provider_id and are ISO-parseable")

    @pytest.mark.asyncio
    async def test_slots_are_in_chronological_order_per_group(self, real_slots):
        """Slots should generally be in forward-chronological order."""
        slots, _ = real_slots
        if len(slots) < 2:
            pytest.skip("Need at least 2 slots to check ordering")

        out_of_order = 0
        for i in range(1, len(slots)):
            if slots[i].start < slots[i - 1].start:
                out_of_order += 1

        print(f"✓ {len(slots)} slots checked. {out_of_order} out-of-order pairs (cross-provider expected)")


class TestRealSlotsFiltering:
    """Filter REAL NexHealth slots through our clinic hours service."""

    @pytest.mark.asyncio
    async def test_no_hours_configured_passes_all(self, real_slots):
        """With no operating hours, all real slots pass through."""
        slots, tz = real_slots
        result = filter_slots(slots, operating_hours=[], breaks=[], timezone=tz)
        assert len(result) == len(slots)
        print(f"✓ All {len(slots)} slots passed (no hours configured)")

    @pytest.mark.asyncio
    async def test_standard_business_hours_filters_after_hours(self, real_slots):
        """
        Configure 9 AM – 5 PM Mon-Fri, closed weekends.
        Any real slots outside those windows should be removed.
        """
        slots, tz = real_slots
        if not slots:
            pytest.skip("No slots to filter")

        # Standard business hours: 9-5 Mon-Fri
        hours = []
        for day in range(7):
            if day < 5:
                hours.append(FakeOperatingHours(day, True, time(9, 0), time(17, 0)))
            else:
                hours.append(FakeOperatingHours(day, False))

        filtered = filter_slots(slots, operating_hours=hours, breaks=[], timezone=tz)
        removed = len(slots) - len(filtered)

        print(f"\n✓ Standard hours filter (9-5 Mon-Fri):")
        print(f"  Input:   {len(slots)} slots")
        print(f"  Output:  {len(filtered)} slots")
        print(f"  Removed: {removed} slots")

        # Verify all remaining slots are within business hours
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(tz)
        for s in filtered:
            local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
            local_end = datetime.fromisoformat(s.end).astimezone(local_tz)

            assert local_start.weekday() < 5, f"Weekend slot leaked through: {s.start}"
            assert local_start.time() >= time(9, 0), f"Pre-9AM slot leaked: {s.start} → local {local_start.time()}"
            assert local_end.time() <= time(17, 0), f"Post-5PM slot leaked: {s.end} → local {local_end.time()}"

        print(f"  ✓ All {len(filtered)} remaining slots verified within 9 AM – 5 PM, Mon-Fri")

    @pytest.mark.asyncio
    async def test_lunch_break_filters_midday_slots(self, real_slots):
        """
        Add a 12:00–13:00 lunch break. Slots in that window should be removed.
        """
        slots, tz = real_slots
        if not slots:
            pytest.skip("No slots to filter")

        # Wide open hours (6 AM – 10 PM) to isolate break filtering
        hours = [FakeOperatingHours(day, True, time(6, 0), time(22, 0)) for day in range(7)]
        breaks = [FakeBreak("Lunch", day_of_week=None, start_time=time(12, 0), end_time=time(13, 0))]

        filtered = filter_slots(slots, operating_hours=hours, breaks=breaks, timezone=tz)
        removed = len(slots) - len(filtered)

        print(f"\n✓ Lunch break filter (12:00-13:00):")
        print(f"  Input:   {len(slots)} slots")
        print(f"  Output:  {len(filtered)} slots")
        print(f"  Removed: {removed} slots (lunch overlap)")

        # Verify no remaining slot overlaps lunch
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(tz)
        lunch_start = time(12, 0)
        lunch_end = time(13, 0)

        for s in filtered:
            local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
            local_end = datetime.fromisoformat(s.end).astimezone(local_tz)
            s_start_t = local_start.time()
            s_end_t = local_end.time()

            overlaps = s_start_t < lunch_end and s_end_t > lunch_start
            assert not overlaps, (
                f"Slot {s.start}→{s.end} (local {s_start_t}→{s_end_t}) "
                f"overlaps lunch {lunch_start}→{lunch_end}"
            )

        print(f"  ✓ All {len(filtered)} remaining slots verified no lunch overlap")

    @pytest.mark.asyncio
    async def test_narrow_window_filters_most_slots(self, real_slots):
        """
        Set a very narrow window (10 AM – 11 AM) and verify most slots are removed.
        """
        slots, tz = real_slots
        if not slots:
            pytest.skip("No slots to filter")

        hours = [FakeOperatingHours(day, True, time(10, 0), time(11, 0)) for day in range(5)]
        # Weekend closed
        hours.extend([FakeOperatingHours(5, False), FakeOperatingHours(6, False)])

        filtered = filter_slots(slots, operating_hours=hours, breaks=[], timezone=tz)
        removed = len(slots) - len(filtered)

        print(f"\n✓ Narrow window filter (10-11 AM Mon-Fri):")
        print(f"  Input:   {len(slots)}")
        print(f"  Output:  {len(filtered)}")
        print(f"  Removed: {removed}")

        # With a 1-hour daily window, we expect a large percentage removed
        if len(slots) > 10:
            assert len(filtered) < len(slots), "Narrow window should remove some slots"
            print(f"  ✓ Filter removed {removed / len(slots) * 100:.0f}% of slots (as expected)")

    @pytest.mark.asyncio
    async def test_combined_hours_and_breaks(self, real_slots):
        """
        Realistic dental clinic schedule:
          - Mon-Fri 8 AM – 6 PM
          - Lunch break 12:30 – 1:30 PM
          - Sat 9 AM – 1 PM (no lunch)
          - Sun closed
        """
        slots, tz = real_slots
        if not slots:
            pytest.skip("No slots to filter")

        hours = [
            FakeOperatingHours(0, True, time(8, 0), time(18, 0)),   # Mon
            FakeOperatingHours(1, True, time(8, 0), time(18, 0)),   # Tue
            FakeOperatingHours(2, True, time(8, 0), time(18, 0)),   # Wed
            FakeOperatingHours(3, True, time(8, 0), time(18, 0)),   # Thu
            FakeOperatingHours(4, True, time(8, 0), time(18, 0)),   # Fri
            FakeOperatingHours(5, True, time(9, 0), time(13, 0)),   # Sat
            FakeOperatingHours(6, False),                            # Sun
        ]
        # Lunch only Mon-Fri
        breaks = [FakeBreak("Lunch", day_of_week=d, start_time=time(12, 30), end_time=time(13, 30)) for d in range(5)]

        filtered = filter_slots(slots, operating_hours=hours, breaks=breaks, timezone=tz)
        removed = len(slots) - len(filtered)

        print(f"\n✓ Realistic dental schedule:")
        print(f"  Input:   {len(slots)}")
        print(f"  Output:  {len(filtered)}")
        print(f"  Removed: {removed}")

        # Verify all remaining slots respect the schedule
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(tz)

        for s in filtered:
            local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
            local_end = datetime.fromisoformat(s.end).astimezone(local_tz)
            day = local_start.weekday()

            assert day < 6, f"Sunday slot leaked: {s.start}"

            if day < 5:
                assert local_start.time() >= time(8, 0), f"Pre-8AM weekday slot: {local_start}"
                assert local_end.time() <= time(18, 0), f"Post-6PM weekday slot: {local_end}"
                # Verify no lunch overlap on weekdays
                overlaps_lunch = local_start.time() < time(13, 30) and local_end.time() > time(12, 30)
                assert not overlaps_lunch, f"Weekday lunch overlap: {local_start.time()}→{local_end.time()}"
            else:
                assert local_start.time() >= time(9, 0), f"Pre-9AM Saturday slot: {local_start}"
                assert local_end.time() <= time(13, 0), f"Post-1PM Saturday slot: {local_end}"

        print(f"  ✓ All {len(filtered)} remaining slots verified against full schedule")

    @pytest.mark.asyncio
    async def test_filter_is_idempotent(self, real_slots):
        """Running filter twice with same config gives same result."""
        slots, tz = real_slots
        if not slots:
            pytest.skip("No slots to filter")

        hours = [FakeOperatingHours(d, True, time(9, 0), time(17, 0)) for d in range(5)]
        hours.extend([FakeOperatingHours(5, False), FakeOperatingHours(6, False)])
        breaks = [FakeBreak()]

        first_pass = filter_slots(slots, hours, breaks, tz)
        second_pass = filter_slots(first_pass, hours, breaks, tz)

        assert len(first_pass) == len(second_pass), "Filter should be idempotent"
        for a, b in zip(first_pass, second_pass):
            assert a.start == b.start
            assert a.end == b.end

        print(f"✓ Idempotent: {len(first_pass)} slots both passes")


class TestRealSlotsEdgeCases:
    """Edge cases tested against real data."""

    @pytest.mark.asyncio
    async def test_all_days_closed_removes_everything(self, real_slots):
        """If every day is marked closed, no slots should pass."""
        slots, tz = real_slots
        if not slots:
            pytest.skip("No slots to filter")

        hours = [FakeOperatingHours(d, False) for d in range(7)]
        filtered = filter_slots(slots, hours, [], tz)
        assert len(filtered) == 0, f"Expected 0 slots but got {len(filtered)}"
        print(f"✓ All-closed config: {len(slots)} → 0 slots")

    @pytest.mark.asyncio
    async def test_24_7_schedule_passes_everything(self, real_slots):
        """If open 24/7 with no breaks, all slots pass."""
        slots, tz = real_slots

        hours = [FakeOperatingHours(d, True, time(0, 0), time(23, 59)) for d in range(7)]
        filtered = filter_slots(slots, hours, [], tz)
        assert len(filtered) == len(slots), f"Expected {len(slots)} but got {len(filtered)}"
        print(f"✓ 24/7 schedule: all {len(slots)} slots passed")

    @pytest.mark.asyncio
    async def test_multiple_breaks_per_day(self, real_slots):
        """
        Multiple breaks in a day (morning tea + lunch + afternoon tea)
        should all be respected.
        """
        slots, tz = real_slots
        if not slots:
            pytest.skip("No slots to filter")

        hours = [FakeOperatingHours(d, True, time(7, 0), time(20, 0)) for d in range(7)]
        breaks = [
            FakeBreak("Morning Tea", day_of_week=None, start_time=time(10, 0), end_time=time(10, 30)),
            FakeBreak("Lunch", day_of_week=None, start_time=time(12, 0), end_time=time(13, 0)),
            FakeBreak("Afternoon Tea", day_of_week=None, start_time=time(15, 0), end_time=time(15, 30)),
        ]

        filtered = filter_slots(slots, hours, breaks, tz)
        removed = len(slots) - len(filtered)

        print(f"\n✓ Multiple breaks (morning tea, lunch, afternoon tea):")
        print(f"  Input: {len(slots)}, Output: {len(filtered)}, Removed: {removed}")

        # Verify none of the remaining slots overlap any break
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(tz)
        for s in filtered:
            local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
            local_end = datetime.fromisoformat(s.end).astimezone(local_tz)
            s_start_t = local_start.time()
            s_end_t = local_end.time()

            for brk in breaks:
                overlaps = s_start_t < brk.end_time and s_end_t > brk.start_time
                assert not overlaps, (
                    f"Slot {s_start_t}→{s_end_t} overlaps {brk.name} ({brk.start_time}→{brk.end_time})"
                )

        print(f"  ✓ No break overlaps in remaining {len(filtered)} slots")
