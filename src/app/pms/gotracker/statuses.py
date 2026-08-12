"""GoTracker appointment-status semantics shared by sync consumers.

These are Tracker's appointment disposition IDs.  They are distinct from
patient-flow states (for example ``Completed`` in Chair Flow).
"""

from __future__ import annotations

# A patient will not attend an appointment with any of these dispositions.
# Keep no-show separate at the campaign layer if it should enter rebooking, but
# it must never remain eligible for appointment reminders or post-op outreach.
NON_ATTENDING_STATUS_IDS = frozenset({3, 5, 6, 8})


def is_non_attending_status(status_id: int | None) -> bool:
    """Whether a GoTracker status means this visit will not take place."""
    return status_id in NON_ATTENDING_STATUS_IDS
