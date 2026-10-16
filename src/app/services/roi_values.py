"""Resolve a clinic's ROI value inputs (shared by ROI and campaign revenue).

The dashboard ROI tile and the per-campaign revenue attribution must price a
visit the same way, or the two figures argue instead of agreeing. Both resolve
the value here: the location's own config first, else the institution's, as a
whole — mixing a location's appointment value with the institution's hourly
rate would produce a figure that is neither.
"""

from __future__ import annotations

from typing import Any

#: The value inputs a clinic sets. Kept in one place so ROI and revenue agree.
LOCATION_ROI_FIELDS = (
    "avg_appointment_value",
    "avg_new_patient_value",
    "staff_hourly_rate",
    "avg_call_duration_minutes",
)


def resolved_roi_values(
    location: Any, institution: Any
) -> tuple[dict[str, float | None], str] | None:
    """This location's inputs and where they came from, or None if unset."""
    if isinstance(getattr(location, "roi_config", None), dict) and location.roi_config:
        raw, source = location.roi_config, "location"
    elif (
        isinstance(getattr(institution, "roi_config", None), dict)
        and institution.roi_config
    ):
        raw, source = institution.roi_config, "institution"
    else:
        return None
    values: dict[str, float | None] = {
        key: float(raw.get(key) or 0.0) for key in LOCATION_ROI_FIELDS
    }
    # Distinguish "no rate configured" from "a rate of zero".
    rate = raw.get("staff_hourly_rate")
    values["staff_hourly_rate"] = None if rate is None else float(rate)
    return values, source
