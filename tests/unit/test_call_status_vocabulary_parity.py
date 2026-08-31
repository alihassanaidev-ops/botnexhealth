"""The dashboard's call-status registry must match the backend enum.

The two lists are written in different languages and drift silently: a status
added to ``CallStatus`` but not to ``constants.ts`` renders as a raw
``snake_case`` token in the UI, and one listed only in the frontend is a filter
option that can never match a call. Both have shipped before.

This asserts value-for-value parity, and that every entry declares which agent
vocabulary it belongs to so the Tags filter can narrow to the tenant's mode.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.app.models.call import CallStatus

CONSTANTS_TS = (
    Path(__file__).resolve().parents[2]
    / "nexus-dashboard-web"
    / "src"
    / "lib"
    / "constants.ts"
)

#: One `{ value: "x", label: "y", color: "...", scope: "z" }` entry.
_ENTRY = re.compile(
    r'\{\s*value:\s*"(?P<value>[a-z_]+)".*?scope:\s*"(?P<scope>pms|no_pms|both)"\s*\}',
    re.DOTALL,
)

#: Statuses only a PMS agent can produce — it completed a transaction in the
#: practice-management system.
PMS_ONLY = {
    CallStatus.APPOINTMENT_BOOKED.value,
    CallStatus.APPOINTMENT_RESCHEDULED.value,
    CallStatus.APPOINTMENT_CANCELLED.value,
    CallStatus.INSURANCE_VERIFIED.value,
    CallStatus.INSURANCE_UNVERIFIED.value,
}

#: Statuses only a no-PMS agent can produce — requests staff action manually.
NO_PMS_ONLY = {
    CallStatus.NEEDS_BOOKING.value,
    CallStatus.NEEDS_RESCHEDULE.value,
    CallStatus.NEEDS_CANCELLATION.value,
    CallStatus.INSURANCE_AND_BILLING.value,
}


def _frontend_entries() -> dict[str, str]:
    """Map every status value in constants.ts to its declared scope."""
    if not CONSTANTS_TS.exists():
        pytest.skip("dashboard sources not present in this checkout")
    source = CONSTANTS_TS.read_text()
    # Only the STATUS_OPTIONS array — DIRECTION_OPTIONS has no scope field and
    # so is skipped by the pattern anyway, but slice for clarity.
    start = source.index("export const STATUS_OPTIONS")
    end = source.index("export function callStatusFilterOptions", start)
    return {m["value"]: m["scope"] for m in _ENTRY.finditer(source[start:end])}


def test_every_backend_status_is_rendered_by_the_dashboard() -> None:
    listed = _frontend_entries()
    backend = {s.value for s in CallStatus}

    missing = backend - listed.keys()
    assert not missing, (
        f"CallStatus values with no entry in constants.ts: {sorted(missing)}. "
        "They would render as raw snake_case tokens in the dashboard."
    )

    unknown = listed.keys() - backend
    assert not unknown, (
        f"constants.ts lists statuses the backend cannot produce: {sorted(unknown)}. "
        "They would be filter options that never match a call."
    )


def test_pms_and_no_pms_vocabularies_are_partitioned_the_same_way() -> None:
    """The filter narrows on `scope`, so the split must match the enum's."""
    listed = _frontend_entries()

    for value in PMS_ONLY:
        assert listed[value] == "pms", (
            f"{value} is a completed PMS transaction; a no-PMS clinic can "
            f'never have one, so it must be scope "pms", not "{listed[value]}".'
        )

    for value in NO_PMS_ONLY:
        assert listed[value] == "no_pms", (
            f"{value} is a request only a no-PMS agent files; it must be "
            f'scope "no_pms", not "{listed[value]}".'
        )

    shared = {v for v, scope in listed.items() if scope == "both"}
    assert shared == {s.value for s in CallStatus} - PMS_ONLY - NO_PMS_ONLY
