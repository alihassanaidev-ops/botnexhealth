"""Per-location recall campaign configuration.

``InstitutionLocation.recall_config`` is authored JSONB. This module is the one
place that parses it, so the scanner, the API, and the live-call booking guard
all read the same shape and the same defaults.

The configuration carries four things:

* ``approved_recall_type_ids`` — the clinic's allowlist of PMS recall type ids.
  Empty/absent means "no allowlist", which preserves the legacy scanner
  behavior (every due recall subject to the workflow's own trigger filter).
* ``due_within_days`` / ``include_overdue`` — the audience window. Defaults are
  30 days ahead and include overdue, which is what the client asked for.
* ``appointment_type_map`` — recall type id -> bookable appointment type id.
* ``provider_ids`` — providers the agent may book with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_DUE_WITHIN_DAYS = 30
DEFAULT_INCLUDE_OVERDUE = True
MAX_DUE_WITHIN_DAYS = 365


@dataclass(frozen=True)
class RecallConfig:
    approved_recall_type_ids: tuple[str, ...] = ()
    due_within_days: int = DEFAULT_DUE_WITHIN_DAYS
    include_overdue: bool = DEFAULT_INCLUDE_OVERDUE
    appointment_type_map: dict[str, str] = field(default_factory=dict)
    provider_ids: tuple[str, ...] = ()

    @property
    def has_allowlist(self) -> bool:
        return bool(self.approved_recall_type_ids)

    def allows_recall_type(self, recall_type_id: str | None) -> bool:
        if not self.has_allowlist:
            return True
        if recall_type_id in (None, ""):
            return False
        return str(recall_type_id) in set(self.approved_recall_type_ids)

    def appointment_type_id_for(self, recall_type_id: str | None) -> str | None:
        if recall_type_id in (None, ""):
            return None
        return self.appointment_type_map.get(str(recall_type_id))


def parse_recall_config(raw: Any) -> RecallConfig | None:
    """Parse the stored JSONB, returning None when it is not a mapping."""
    if not isinstance(raw, dict):
        return None
    return RecallConfig(
        approved_recall_type_ids=tuple(
            _normalize_id_list(raw.get("approved_recall_type_ids"))
        ),
        due_within_days=_coerce_due_within_days(raw.get("due_within_days")),
        include_overdue=bool(raw.get("include_overdue", DEFAULT_INCLUDE_OVERDUE)),
        appointment_type_map=_normalize_map(raw.get("appointment_type_map")),
        provider_ids=tuple(_normalize_id_list(raw.get("provider_ids"))),
    )


def recall_config_for_location(location: Any) -> RecallConfig:
    """Return the location's config, or defaults when none is stored.

    Defaults are deliberate rather than "disabled": a call to
    :meth:`RecallConfig.allows_recall_type` on an unconfigured location returns
    True, so the scanner keeps its legacy audience instead of silently
    enrolling nobody.
    """
    return parse_recall_config(getattr(location, "recall_config", None)) or RecallConfig()


def _normalize_id_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    ids: list[str] = []
    for item in value:
        cleaned = str(item).strip()
        if cleaned and cleaned not in ids:
            ids.append(cleaned)
    return ids


def _normalize_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, raw in value.items():
        recall_type_id = str(key).strip()
        appointment_type_id = str(raw).strip() if raw is not None else ""
        if recall_type_id and appointment_type_id:
            normalized[recall_type_id] = appointment_type_id
    return normalized


def _coerce_due_within_days(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DUE_WITHIN_DAYS
    return max(0, min(parsed, MAX_DUE_WITHIN_DAYS))
