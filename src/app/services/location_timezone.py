"""Learn a location's timezone from what its practice software already tells us.

``InstitutionLocation.timezone`` defaults to ``"UTC"`` and nothing populated it,
so a clinic in Ontario silently ran on UTC. Nothing surfaced that until a
campaign deferred: quiet hours read the field correctly, judged 2:40pm local as
6:40pm, and held an outbound call until the next window it thought was open.

GoTracker sends ``timezone`` on every appointment webhook and NexHealth reports
one on the location record — both were parsed and dropped. This puts them to
use, so the common case needs no administrator action at all.

Two rules keep it safe:

* **Never overwrite a deliberate choice.** Only a location still sitting on the
  ``"UTC"`` default is updated. Someone who set UTC on purpose, or set anything
  else, is left alone — we cannot tell a deliberate UTC from an unset one, and
  overwriting a real setting from a payload would be worse than the bug.
* **Never trust the payload blindly.** An unknown zone name is ignored rather
  than stored, because a bad value here silently mis-times every future send.
"""

from __future__ import annotations

import logging
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.app.models.institution_location import InstitutionLocation

logger = logging.getLogger(__name__)

#: The column default. Indistinguishable from "nobody has set this", which is
#: exactly why it is the only value we are willing to replace.
UNSET_TIMEZONE = "UTC"

#: Keys the two practice systems use for the same fact.
_TIMEZONE_KEYS = (
    "timezone",
    "time_zone",
    "Timezone",
    "TimeZone",
    "location_timezone",
)


def extract_timezone(payload: Any) -> str | None:
    """A valid IANA zone from a PMS payload, or None.

    Validated here rather than at the call site: a name we cannot resolve is
    worse than no name, because it would be stored and then quietly fall back to
    UTC on every read.
    """
    if not isinstance(payload, dict):
        return None
    for key in _TIMEZONE_KEYS:
        raw = payload.get(key)
        if not isinstance(raw, str):
            continue
        name = raw.strip()
        if not name:
            continue
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("ignoring unrecognised timezone %r from PMS payload", name)
            continue
        return name
    return None


def learn_location_timezone(
    location: InstitutionLocation,
    payload: Any,
) -> str | None:
    """Adopt the payload's timezone if this location has never had one set.

    Returns the newly-applied zone, or None when nothing changed. The caller
    owns the commit, so this stays usable inside a webhook's existing
    transaction rather than opening one of its own.
    """
    current = (getattr(location, "timezone", None) or "").strip()
    if current and current != UNSET_TIMEZONE:
        return None

    learned = extract_timezone(payload)
    if learned is None or learned == current:
        return None

    location.timezone = learned
    logger.info(
        "location %s timezone learned from PMS payload: %s -> %s",
        getattr(location, "id", "?"),
        current or "(empty)",
        learned,
    )
    return learned


def is_timezone_unset(location: InstitutionLocation) -> bool:
    """Whether this location is still on the default.

    Used by the launch checklist: a campaign that sends on a schedule, or
    respects quiet hours, behaves wrongly on a location that never had its
    timezone confirmed — and does so silently, which is the part worth warning
    about.
    """
    current = (getattr(location, "timezone", None) or "").strip()
    return not current or current == UNSET_TIMEZONE


__all__ = [
    "UNSET_TIMEZONE",
    "extract_timezone",
    "is_timezone_unset",
    "learn_location_timezone",
]
