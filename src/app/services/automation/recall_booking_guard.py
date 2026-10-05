"""Server-side booking constraints for a live recall campaign call.

The AI agent decides what to book during the call, so the prompt alone cannot be
the guard: an agent that offers "any appointment type" would happily book one.
The outbound voice step stamps ``workflow_run_id`` on the Retell call and the
voice attempt carries ``retell_call_id``; from the call id this resolves the
campaign run, its location, and that location's ``recall_config``.

When a call belongs to a recall campaign the configured constraints are
enforced by the booking handlers:

* only the appointment type mapped for the run's recall type may be offered or
  booked (an unconfigured mapping fails closed);
* only the configured providers may be booked;
* the earliest bookable date is ``max(today, recall due date)``.

A call with no recall voice attempt returns ``None`` and is left completely
untouched, so inbound and non-recall outbound calls are unaffected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select

from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.institution_location import InstitutionLocation
from src.app.models.outbound_voice import WorkflowVoiceAttempt
from src.app.services.automation.recall_config import parse_recall_config

logger = logging.getLogger(__name__)

_SOURCE_PREFIXES = ("nh-", "gt-")


def _normalize_source_id(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for prefix in _SOURCE_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


@dataclass(frozen=True)
class RecallBookingConstraints:
    recall_type_id: str | None
    appointment_type_ids: frozenset[str]
    provider_ids: frozenset[str]
    earliest_date: date | None

    def permits_appointment_type(self, appointment_type_id: object) -> bool:
        # Fail closed: a recall campaign with no approved-type mapping must not
        # let the agent book whatever type it likes.
        if not self.appointment_type_ids:
            return False
        return _normalize_source_id(appointment_type_id) in self.appointment_type_ids

    def permits_provider(self, provider_id: object) -> bool:
        if not self.provider_ids:
            return True
        normalized = _normalize_source_id(provider_id)
        return normalized is not None and normalized in self.provider_ids


def _location_today(location: InstitutionLocation | None) -> date:
    timezone_name = getattr(location, "timezone", None)
    try:
        if isinstance(timezone_name, str) and timezone_name.strip():
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(timezone_name.strip())).date()
    except Exception:  # noqa: BLE001 - bad zone must not break the guard
        pass
    return datetime.now(timezone.utc).date()


def _earliest_booking_date(
    metadata: dict, location: InstitutionLocation | None
) -> date:
    today = _location_today(location)
    raw_due = metadata.get("recall_due_date")
    if not raw_due:
        return today
    try:
        due = datetime.fromisoformat(str(raw_due).replace("Z", "+00:00")).date()
    except ValueError:
        return today
    return max(due, today)


async def resolve_recall_constraints_for_call(session, call_id: str | None):
    """Return constraints when this call is part of a recall campaign."""
    if not call_id:
        return None

    run_id = (
        await session.execute(
            select(WorkflowVoiceAttempt.workflow_run_id)
            .where(WorkflowVoiceAttempt.retell_call_id == str(call_id))
            .limit(1)
        )
    ).scalar_one_or_none()
    if not run_id:
        return None

    run = await session.get(AutomationWorkflowRun, run_id)
    if run is None:
        return None

    location = None
    if run.location_id:
        location = await session.get(InstitutionLocation, run.location_id)
    config = (
        parse_recall_config(getattr(location, "recall_config", None))
        if location is not None
        else None
    )
    if config is None:
        return None

    metadata = run.trigger_metadata if isinstance(run.trigger_metadata, dict) else {}
    raw_recall_type = metadata.get("recall_type_id")
    recall_type_id = (
        str(raw_recall_type) if raw_recall_type not in (None, "") else None
    )

    mapped = config.appointment_type_id_for(recall_type_id)
    allowed_types = {mapped} if mapped else set(config.appointment_type_map.values())
    normalized_types = frozenset(
        value
        for value in (_normalize_source_id(item) for item in allowed_types)
        if value
    )
    normalized_providers = frozenset(
        value
        for value in (_normalize_source_id(item) for item in config.provider_ids)
        if value
    )

    return RecallBookingConstraints(
        recall_type_id=recall_type_id,
        appointment_type_ids=normalized_types,
        provider_ids=normalized_providers,
        earliest_date=_earliest_booking_date(metadata, location),
    )
