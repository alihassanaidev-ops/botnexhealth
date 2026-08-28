"""GoTracker appointment-status semantics shared by sync consumers.

These are Tracker's appointment disposition IDs.  They are distinct from
patient-flow states (for example ``Completed`` in Chair Flow).

This module is the single definition. The map used to be duplicated as a private
dict inside the GoTracker webhook route and again as a literal array in the
workflow builder's React config panel, so a label could be corrected in one place
and stay wrong in the others, and nothing anywhere recorded which dispositions
Nexus is allowed to write back.

``semantics`` is the PMS-neutral meaning. Campaign definitions should branch on
it rather than on the numeric id wherever possible, so the same workflow can be
carried to another PMS later.

.. warning::

   ``writable`` is **not yet verified** against the Synchronizer. Every status is
   currently declared writable, which reproduces today's behaviour exactly — the
   schema permits any id in 1..9 and the Synchronizer rejects the ones it will
   not accept. The field exists so that, once the writable set is confirmed
   against the installed Synchronizer build (12.02.260603.1 at the time of
   writing), the builder can grey out an unsupported write at authoring time
   instead of failing mid-run. Narrow ``_UNVERIFIED_WRITABLE`` when confirmed;
   nothing else needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

StatusSemantics = Literal[
    "booked",
    "waiting",
    "late",
    "cancelled",
    "no_show",
    "pending",
]


@dataclass(frozen=True)
class GoTrackerStatus:
    """One Tracker appointment disposition."""

    id: int
    key: str
    label: str
    semantics: StatusSemantics
    # Whether Nexus may set this disposition through the Synchronizer.
    # See the module warning: currently unverified, so every status reports
    # True and behaviour is unchanged from before this catalog existed.
    writable: bool
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "label": self.label,
            "semantics": self.semantics,
            "writable": self.writable,
            "readable": True,
            "description": self.description,
        }


# Placeholder until the writable disposition set is confirmed against the
# installed Synchronizer build. See the module warning.
_UNVERIFIED_WRITABLE = True


GOTRACKER_STATUSES: tuple[GoTrackerStatus, ...] = (
    GoTrackerStatus(
        id=1,
        key="booked",
        label="Booked",
        semantics="booked",
        writable=_UNVERIFIED_WRITABLE,
        description="Scheduled and expected to attend.",
    ),
    GoTrackerStatus(
        id=2,
        key="booked_waiting",
        label="Booked + Waiting",
        semantics="waiting",
        writable=_UNVERIFIED_WRITABLE,
        description="Scheduled, and the patient is also on the waiting list.",
    ),
    GoTrackerStatus(
        id=3,
        key="cancelled",
        label="Cancelled",
        semantics="cancelled",
        writable=_UNVERIFIED_WRITABLE,
        description="Cancelled by the patient.",
    ),
    GoTrackerStatus(
        id=4,
        key="late",
        label="Late",
        semantics="late",
        writable=_UNVERIFIED_WRITABLE,
        description="Patient arrived late. Set by the front desk in Tracker.",
    ),
    GoTrackerStatus(
        id=5,
        key="no_show",
        label="No Show",
        semantics="no_show",
        writable=_UNVERIFIED_WRITABLE,
        description="Patient did not attend.",
    ),
    GoTrackerStatus(
        id=6,
        key="office_cancel",
        label="Office Cancel",
        semantics="cancelled",
        writable=_UNVERIFIED_WRITABLE,
        description="Cancelled by the practice.",
    ),
    GoTrackerStatus(
        id=7,
        key="pending",
        label="Pending",
        semantics="pending",
        writable=_UNVERIFIED_WRITABLE,
        description="Awaiting confirmation inside Tracker.",
    ),
    GoTrackerStatus(
        id=8,
        key="short_cancel",
        label="Short Cancel",
        semantics="cancelled",
        writable=_UNVERIFIED_WRITABLE,
        description="Cancelled at short notice.",
    ),
    GoTrackerStatus(
        id=9,
        key="waiting",
        label="Waiting",
        semantics="waiting",
        writable=_UNVERIFIED_WRITABLE,
        description="On the waiting list without a booked slot.",
    ),
)

_BY_ID: dict[int, GoTrackerStatus] = {status.id: status for status in GOTRACKER_STATUSES}

MIN_STATUS_ID = min(_BY_ID)
MAX_STATUS_ID = max(_BY_ID)

# A patient will not attend an appointment with any of these dispositions.
# Keep no-show separate at the campaign layer if it should enter rebooking, but
# it must never remain eligible for appointment reminders or post-op outreach.
NON_ATTENDING_STATUS_IDS = frozenset(
    status.id
    for status in GOTRACKER_STATUSES
    if status.semantics in {"cancelled", "no_show"}
)

WRITABLE_STATUS_IDS = frozenset(
    status.id for status in GOTRACKER_STATUSES if status.writable
)


def is_non_attending_status(status_id: int | None) -> bool:
    """Whether a GoTracker status means this visit will not take place."""
    return status_id in NON_ATTENDING_STATUS_IDS


def status_for_id(status_id: int | str | None) -> GoTrackerStatus | None:
    """Look up a disposition by id, tolerating the string form webhooks send."""
    if status_id is None or isinstance(status_id, bool):
        return None
    try:
        return _BY_ID.get(int(status_id))
    except (TypeError, ValueError):
        return None


def status_label(status_id: int | str | None) -> str | None:
    """Normalized snake_case label, or the raw id when unrecognised.

    Kept snake_case because it is written into workflow run context as
    ``appointment_status`` and existing published campaign definitions compare
    against those exact values (for example ``"booked"``).
    """
    status = status_for_id(status_id)
    if status is not None:
        return status.key
    return None if status_id in (None, "") else str(status_id)


def is_writable_status(status_id: int | None) -> bool:
    """Whether Nexus may write this disposition back to Tracker."""
    return status_id in WRITABLE_STATUS_IDS


def public_statuses() -> list[dict[str, Any]]:
    """Serializable catalog for the API and the workflow builder."""
    return [status.as_dict() for status in GOTRACKER_STATUSES]
