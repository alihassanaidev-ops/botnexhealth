"""
Integration tests for DURATION-AWARE slot filtering with real NexHealth API.

Tests the critical edge case:
  - A 60-min appointment type should NOT be bookable if it would
    bleed into a break or past the clinic's closing time.
  - Compare slot behavior with vs without appointment_type_id.
  - Verify our filter correctly handles slots of different durations.

Run:
    .venv/bin/python -m pytest tests/integration/test_slot_duration_edge_cases.py -v -s
"""

from __future__ import annotations

import logging
import os
from datetime import time, date, datetime, timedelta

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

if os.getenv("RUN_LIVE_NEXHEALTH") != "1":
    pytest.skip(
        "Live NexHealth tests disabled. Set RUN_LIVE_NEXHEALTH=1 to enable.",
        allow_module_level=True,
    )

from src.app.config import settings
from src.app.nexhealth.client import NexHealthClient
from src.app.api.helpers import handle_nexhealth_request
from src.app.pms.nexhealth.mappers import to_slot
from src.app.pms.models import UniversalSlot
from src.app.services.slot_filter import filter_slots

logger = logging.getLogger(__name__)

TARGET_CLINIC_NAME = "Relaxation Dental 2"


# ---------------------------------------------------------------------------
# Lightweight stand-ins for ORM models
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
    async with NexHealthClient(settings) as client:
        yield client


@pytest_asyncio.fixture
async def clinic_info(nh_client):
    """Discover Relaxation Dental 2 metadata."""
    raw = await handle_nexhealth_request(nh_client, "GET", "/institutions", params={"per_page": 50})
    institutions = raw.get("data", [])

    subdomain = location_id = timezone = None
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
        pytest.skip(f"'{TARGET_CLINIC_NAME}' not found")

    # Get providers
    prov_raw = await handle_nexhealth_request(
        nh_client, "GET", "/providers",
        params={"subdomain": subdomain, "location_id": location_id, "page": 1, "per_page": 10}
    )
    providers = prov_raw.get("data", [])
    provider_id = providers[0]["id"] if providers else None

    # Get appointment types
    appt_raw = await handle_nexhealth_request(
        nh_client, "GET", "/appointment_types",
        params={"subdomain": subdomain, "location_id": location_id}
    )
    appt_types = appt_raw.get("data", [])

    print(f"\n✓ Clinic: {TARGET_CLINIC_NAME}")
    print(f"  subdomain={subdomain}, location_id={location_id}, tz={timezone}")
    print(f"  Providers: {len(providers)}")
    print(f"  Appointment Types ({len(appt_types)}):")
    for at in appt_types:
        mins = at.get("minutes") or at.get("duration") or "?"
        print(f"    - {at.get('name')} (id={at.get('id')}, {mins} min)")

    return {
        "subdomain": subdomain,
        "location_id": location_id,
        "provider_id": provider_id,
        "timezone": timezone,
        "client": nh_client,
        "appointment_types": appt_types,
        "providers": providers,
    }


async def _fetch_slots(
    info: dict,
    appointment_type_id: int | None = None,
    slot_length: int | None = None,
    provider_id: int | None = None,
) -> list[UniversalSlot]:
    """Fetch real slots with optional appointment_type_id or slot_length."""
    params = {
        "subdomain": info["subdomain"],
        "start_date": date.today().isoformat(),
        "days": 7,
        "lids[]": [info["location_id"]],
    }
    pid = provider_id or info["provider_id"]
    if pid:
        params["pids[]"] = [pid]
    if appointment_type_id:
        params["appointment_type_id"] = appointment_type_id
    if slot_length:
        params["slot_length"] = slot_length

    raw = await handle_nexhealth_request(info["client"], "GET", "/appointment_slots", params=params)

    slots: list[UniversalSlot] = []
    for group in raw.get("data", []):
        group_pid = group.get("pid")
        group_lid = group.get("lid")
        for slot in group.get("slots", []):
            slot["_pid"] = group_pid
            slot["_lid"] = group_lid
            slots.append(to_slot(slot))

    return slots


# ===========================================================================
# Tests: Explore NexHealth Behavior
# ===========================================================================


class TestNexHealthSlotDurationBehavior:
    """
    Probe how NexHealth adjusts slot start/end times with different
    appointment_type_id and slot_length values.
    """

    @pytest.mark.asyncio
    async def test_default_slots_duration(self, clinic_info):
        """
        Fetch slots WITHOUT appointment_type_id (default 15-min).
        Observe the slot duration NexHealth returns.
        """
        slots = await _fetch_slots(clinic_info)
        if not slots:
            pytest.skip("No slots available")

        # Analyze durations
        durations = []
        for s in slots[:20]:
            start = datetime.fromisoformat(s.start)
            end = datetime.fromisoformat(s.end)
            dur = (end - start).total_seconds() / 60
            durations.append(dur)

        print(f"\n✓ Default slots (no appointment type):")
        print(f"  Total: {len(slots)} slots")
        print(f"  Duration of first 20 slots (mins): {durations}")
        print(f"  Min/Max duration: {min(durations):.0f}/{max(durations):.0f} mins")

    @pytest.mark.asyncio
    async def test_slots_with_appointment_type(self, clinic_info):
        """
        Fetch slots WITH an appointment_type_id.
        The end time should reflect the appointment type duration.
        """
        info = clinic_info
        appt_types = info["appointment_types"]
        if not appt_types:
            pytest.skip("No appointment types found")

        # Try each appointment type and compare durations
        for at in appt_types[:3]:  # test first 3 types
            at_id = at.get("id")
            at_name = at.get("name")
            at_minutes = at.get("minutes") or at.get("duration")

            slots = await _fetch_slots(info, appointment_type_id=at_id)
            if not slots:
                print(f"  ⚠ No slots for appointment type '{at_name}' (id={at_id})")
                continue

            # Measure durations
            durations = []
            for s in slots[:10]:
                start = datetime.fromisoformat(s.start)
                end = datetime.fromisoformat(s.end)
                dur = (end - start).total_seconds() / 60
                durations.append(dur)

            print(f"\n✓ Appointment Type: '{at_name}' (id={at_id}, expected={at_minutes} min)")
            print(f"  Slots: {len(slots)}")
            print(f"  Actual durations (first 10): {durations}")
            print(f"  Min/Max: {min(durations):.0f}/{max(durations):.0f} mins")

            # Verify NexHealth returns end_time reflecting the full duration
            if at_minutes:
                for dur in durations:
                    assert dur >= float(at_minutes), (
                        f"Slot duration {dur} min < appointment type {at_minutes} min — "
                        f"NexHealth may not be applying appointment_type_id correctly"
                    )
                print(f"  ✓ All durations >= {at_minutes} min (NexHealth applies type duration)")

    @pytest.mark.asyncio
    async def test_slots_with_explicit_slot_length(self, clinic_info):
        """
        Fetch slots with slot_length=60 to force 60-min slot windows.
        Compare against default 15-min slots.
        """
        info = clinic_info

        # Default slots (15 min)
        default_slots = await _fetch_slots(info)
        # 60-min slots
        long_slots = await _fetch_slots(info, slot_length=60)

        if not default_slots or not long_slots:
            pytest.skip("No slots for comparison")

        # Analyze 60-min slot durations
        long_durations = []
        for s in long_slots[:10]:
            start = datetime.fromisoformat(s.start)
            end = datetime.fromisoformat(s.end)
            dur = (end - start).total_seconds() / 60
            long_durations.append(dur)

        print(f"\n✓ Comparison:")
        print(f"  Default (15-min): {len(default_slots)} slots")
        print(f"  60-min length:    {len(long_slots)} slots")
        print(f"  60-min actual durations (first 10): {long_durations}")

        # 60-min slots should all be >= 60 min duration
        for dur in long_durations:
            assert dur >= 60, f"Slot with slot_length=60 has duration {dur} min"

        # Fewer 60-min slots than 15-min slots (more restrictive)
        print(f"  ✓ All 60-min slots verified. Count difference: {len(default_slots) - len(long_slots)}")


# ===========================================================================
# Tests: Duration-Aware Filter Edge Cases
# ===========================================================================


class TestDurationFilterNearBreak:
    """
    Core edge case: 60-min slot that starts before break should be rejected
    if the slot would extend into the break.
    """

    @pytest.mark.asyncio
    async def test_long_slot_before_break_gets_filtered(self, clinic_info):
        """
        With 60-min slots and a 12:00-13:00 break:
        A slot starting at 11:30 with end at 12:30 should be removed
        because it overlaps the break window.
        """
        info = clinic_info
        slots = await _fetch_slots(info, slot_length=60)
        if not slots:
            pytest.skip("No 60-min slots available")

        hours = [FakeOperatingHours(d, True, time(6, 0), time(22, 0)) for d in range(7)]
        breaks = [FakeBreak("Lunch", day_of_week=None, start_time=time(12, 0), end_time=time(13, 0))]

        filtered = filter_slots(slots, hours, breaks, info["timezone"])

        # Find slots that WERE removed by the filter
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(info["timezone"])

        removed_near_break = []
        kept_near_break = []
        for s in slots:
            local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
            local_end = datetime.fromisoformat(s.end).astimezone(local_tz)
            start_t = local_start.time()
            end_t = local_end.time()

            near_lunch = (start_t >= time(11, 0) and start_t < time(13, 30))

            if near_lunch:
                if s in filtered:
                    kept_near_break.append(f"  KEPT: {start_t}→{end_t}")
                else:
                    removed_near_break.append(f"  REMOVED: {start_t}→{end_t}")

        print(f"\n✓ 60-min slots near lunch break (12:00-13:00):")
        print(f"  Total slots: {len(slots)}, Filtered: {len(filtered)}")
        for line in removed_near_break[:10]:
            print(line)
        for line in kept_near_break[:10]:
            print(line)

        # Verify: no filtered slot overlaps the break
        for s in filtered:
            local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
            local_end = datetime.fromisoformat(s.end).astimezone(local_tz)
            start_t = local_start.time()
            end_t = local_end.time()

            overlaps = start_t < time(13, 0) and end_t > time(12, 0)
            assert not overlaps, f"60-min slot {start_t}→{end_t} overlaps break 12:00-13:00"

        print(f"  ✓ All {len(filtered)} filtered slots verified: no lunch overlap")

    @pytest.mark.asyncio
    async def test_long_slot_before_close_gets_filtered(self, clinic_info):
        """
        With 60-min slots and clinic closing at 17:00:
        A slot starting at 16:30 (ending 17:30) should be removed
        because it extends past closing time.
        """
        info = clinic_info
        slots = await _fetch_slots(info, slot_length=60)
        if not slots:
            pytest.skip("No 60-min slots available")

        # Clinic closes at 5 PM
        hours = [FakeOperatingHours(d, True, time(7, 0), time(17, 0)) for d in range(5)]
        hours.extend([FakeOperatingHours(5, False), FakeOperatingHours(6, False)])

        filtered = filter_slots(slots, hours, [], info["timezone"])

        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(info["timezone"])

        # Check what happened to late-afternoon slots
        late_slots_removed = []
        late_slots_kept = []
        for s in slots:
            local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
            local_end = datetime.fromisoformat(s.end).astimezone(local_tz)
            start_t = local_start.time()
            end_t = local_end.time()

            if local_start.weekday() < 5 and start_t >= time(16, 0):
                if s in filtered:
                    late_slots_kept.append(f"  KEPT: {start_t}→{end_t}")
                else:
                    late_slots_removed.append(f"  REMOVED: {start_t}→{end_t}")

        print(f"\n✓ 60-min slots near close (17:00):")
        print(f"  Total: {len(slots)}, Filtered: {len(filtered)}")
        for line in late_slots_removed[:10]:
            print(line)
        for line in late_slots_kept[:5]:
            print(line)

        # Verify: no filtered slot ends after 17:00
        for s in filtered:
            local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
            local_end = datetime.fromisoformat(s.end).astimezone(local_tz)
            if local_start.weekday() < 5:
                assert local_end.time() <= time(17, 0), (
                    f"60-min slot {local_start.time()}→{local_end.time()} extends past 17:00 close"
                )

        print(f"  ✓ All filtered slots end on or before 17:00")


class TestDurationFilterWithRealAppointmentTypes:
    """
    Use ACTUAL appointment types from the NexHealth sandbox
    to test filtering with real durations.
    """

    @pytest.mark.asyncio
    async def test_each_appointment_type_near_break(self, clinic_info):
        """
        For each appointment type, fetch slots, apply a lunch break,
        and verify no filtered slot overlaps the break.
        """
        info = clinic_info
        appt_types = info["appointment_types"]
        if not appt_types:
            pytest.skip("No appointment types")

        hours = [FakeOperatingHours(d, True, time(7, 0), time(20, 0)) for d in range(7)]
        breaks = [FakeBreak("Lunch", day_of_week=None, start_time=time(12, 0), end_time=time(13, 0))]

        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(info["timezone"])

        for at in appt_types[:4]:
            at_id = at.get("id")
            at_name = at.get("name")
            at_minutes = at.get("minutes") or at.get("duration") or "?"

            slots = await _fetch_slots(info, appointment_type_id=at_id)
            if not slots:
                print(f"\n  ⚠ Skipping '{at_name}' — no slots")
                continue

            filtered = filter_slots(slots, hours, breaks, info["timezone"])
            removed = len(slots) - len(filtered)

            # Verify
            for s in filtered:
                local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
                local_end = datetime.fromisoformat(s.end).astimezone(local_tz)
                st = local_start.time()
                et = local_end.time()
                overlaps = st < time(13, 0) and et > time(12, 0)
                assert not overlaps, (
                    f"[{at_name}/{at_minutes}min] {st}→{et} overlaps lunch 12:00-13:00"
                )

            print(f"\n✓ '{at_name}' ({at_minutes} min): {len(slots)}→{len(filtered)} "
                  f"(-{removed}, no lunch overlap)")

    @pytest.mark.asyncio
    async def test_each_appointment_type_near_close(self, clinic_info):
        """
        For each appointment type, apply a 17:00 close and verify
        no slot extends past closing time with its full duration.
        """
        info = clinic_info
        appt_types = info["appointment_types"]
        if not appt_types:
            pytest.skip("No appointment types")

        hours = [FakeOperatingHours(d, True, time(7, 0), time(17, 0)) for d in range(5)]
        hours.extend([FakeOperatingHours(5, False), FakeOperatingHours(6, False)])

        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(info["timezone"])

        for at in appt_types[:4]:
            at_id = at.get("id")
            at_name = at.get("name")
            at_minutes = at.get("minutes") or at.get("duration") or "?"

            slots = await _fetch_slots(info, appointment_type_id=at_id)
            if not slots:
                print(f"\n  ⚠ Skipping '{at_name}' — no slots")
                continue

            filtered = filter_slots(slots, hours, [], info["timezone"])
            removed = len(slots) - len(filtered)

            # Verify no slot ends after 17:00
            for s in filtered:
                local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
                local_end = datetime.fromisoformat(s.end).astimezone(local_tz)
                if local_start.weekday() < 5:
                    assert local_end.time() <= time(17, 0), (
                        f"[{at_name}/{at_minutes}min] ends at {local_end.time()}, past 17:00"
                    )

            print(f"\n✓ '{at_name}' ({at_minutes} min): {len(slots)}→{len(filtered)} "
                  f"(-{removed}, all end ≤17:00)")


class TestDurationComparisonShortVsLong:
    """
    Compare how many slots survive filtering for short vs long appointment types.
    Longer appointments should have fewer valid slots.
    """

    @pytest.mark.asyncio
    async def test_longer_duration_means_fewer_viable_slots(self, clinic_info):
        """
        Given tight clinic hours (8:00-17:00) + lunch break:
        - 15-min default slots should have the most options
        - 30-min slots have fewer
        - 60-min slots have even fewer
        """
        info = clinic_info

        hours = [FakeOperatingHours(d, True, time(8, 0), time(17, 0)) for d in range(5)]
        hours.extend([FakeOperatingHours(5, False), FakeOperatingHours(6, False)])
        breaks = [FakeBreak("Lunch", day_of_week=None, start_time=time(12, 0), end_time=time(13, 0))]

        # Fetch at different slot_lengths
        results = {}
        for length in [15, 30, 60]:
            slots = await _fetch_slots(info, slot_length=length)
            filtered = filter_slots(slots, hours, breaks, info["timezone"])
            results[length] = {"raw": len(slots), "filtered": len(filtered)}
            print(f"  slot_length={length}: raw={len(slots)}, filtered={len(filtered)}")

        print(f"\n✓ Duration comparison (8-5 + lunch):")
        for k, v in results.items():
            print(f"  {k}-min: {v['raw']} raw → {v['filtered']} filtered")

        # With tighter constraints, longer slots should have equal or fewer results
        if results[15]["filtered"] > 0 and results[60]["filtered"] > 0:
            assert results[60]["filtered"] <= results[15]["filtered"], (
                f"60-min ({results[60]['filtered']}) should have ≤ slots than 15-min ({results[15]['filtered']})"
            )
            print(f"  ✓ Confirmed: 60-min has fewer or equal filtered slots than 15-min")

    @pytest.mark.asyncio
    async def test_very_long_appointment_near_break_and_close(self, clinic_info):
        """
        Simulate a 90-min appointment with tight schedule:
        - Opens 8:00, closes 17:00
        - Break 12:00-13:00
        
        The last valid start for 90-min before break = 10:30 (ends 12:00)
        The last valid start before close = 15:30 (ends 17:00)
        Slots starting at 11:00 (would end 12:30, overlapping break) → removed
        Slots starting at 16:00 (would end 17:30, past close) → removed
        """
        info = clinic_info

        slots = await _fetch_slots(info, slot_length=90)
        if not slots:
            pytest.skip("No 90-min slots available")

        hours = [FakeOperatingHours(d, True, time(8, 0), time(17, 0)) for d in range(5)]
        hours.extend([FakeOperatingHours(5, False), FakeOperatingHours(6, False)])
        breaks = [FakeBreak("Lunch", day_of_week=None, start_time=time(12, 0), end_time=time(13, 0))]

        filtered = filter_slots(slots, hours, breaks, info["timezone"])

        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(info["timezone"])

        print(f"\n✓ 90-min slots with tight schedule:")
        print(f"  Raw: {len(slots)}, Filtered: {len(filtered)}")

        for s in filtered:
            local_start = datetime.fromisoformat(s.start).astimezone(local_tz)
            local_end = datetime.fromisoformat(s.end).astimezone(local_tz)
            st = local_start.time()
            et = local_end.time()

            if local_start.weekday() < 5:
                # Must start after open
                assert st >= time(8, 0), f"Starts before 8:00: {st}"
                # Must end before close
                assert et <= time(17, 0), f"Ends after 17:00: {et}"
                # Must not overlap lunch
                overlaps_lunch = st < time(13, 0) and et > time(12, 0)
                assert not overlaps_lunch, f"Overlaps lunch: {st}→{et}"

        print(f"  ✓ All {len(filtered)} slots verified: within hours, no break overlap")

        # Print examples of removed slots for visibility
        removed = [s for s in slots if s not in filtered][:5]
        if removed:
            print(f"  Examples of removed slots:")
            for s in removed:
                local_s = datetime.fromisoformat(s.start).astimezone(local_tz)
                local_e = datetime.fromisoformat(s.end).astimezone(local_tz)
                print(f"    ✗ {local_s.time()}→{local_e.time()} (day={local_s.strftime('%A')})")
