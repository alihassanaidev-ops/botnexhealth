"""Dispatch-time revalidation seam (Plan 01 §Technical Considerations / Edge Cases).

A step can become invalid between enrollment and dispatch — most importantly, the
appointment a Confirmation/Reminder targets may have been cancelled or rescheduled.
The dispatcher consults a ``RunRevalidator`` immediately before each send: if it
returns a terminal outcome string, the send is skipped and the run exits with that
outcome (e.g. ``"skipped_cancelled"``); returning None means "still valid, proceed".

The real PMS-backed implementation is provided by Plan 09
(``PmsLiveRevalidationService``); this module ships the protocol and a no-op default
so the engine runs safely until that lands.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.models.automation_workflow import AutomationWorkflowRun

logger = logging.getLogger(__name__)


@runtime_checkable
class RunRevalidator(Protocol):
    async def revalidate(self, run: "AutomationWorkflowRun") -> str | None:
        """Return a terminal outcome to skip+exit, or None to proceed with the send."""
        ...


class NoOpRevalidator:
    """Default: never skips. Replaced by Plan 09's PMS live-revalidation service."""

    async def revalidate(self, run: "AutomationWorkflowRun") -> str | None:
        return None


def _parse_dt(value: object) -> datetime | None:
    """Best-effort parse of an ISO-8601 timestamp into an aware datetime."""
    if not isinstance(value, str):
        return None
    try:
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _same_instant(expected: object, current: object) -> bool:
    """True if two timestamps denote the same instant.

    Returns True when either side cannot be parsed — a comparison we cannot
    make must not be treated as a reschedule (fail-open, don't drop the send).
    """
    a, b = _parse_dt(expected), _parse_dt(current)
    if a is None or b is None:
        return True
    return a == b


class PmsLiveRevalidationService:
    """Plan 09 dispatch-time revalidator backed by live NexHealth reads.

    Immediately before an appointment-triggered run sends, this checks the
    appointment's current status in NexHealth:

    * ``"skipped_cancelled"`` — the appointment was cancelled.
    * ``"skipped_rescheduled"`` — its start time no longer matches the time the
      run was enrolled against (``trigger_metadata['appointment_at']``).
    * ``None`` — still valid, proceed with the send.

    Fail-open: any lookup/build error returns ``None`` so a transient NexHealth
    blip never drops a legitimate send (it is logged instead). Recall/manual
    runs carry no appointment ref and short-circuit to ``None``.
    """

    def __init__(self, session: "AsyncSession") -> None:
        self._session = session

    async def revalidate(self, run: "AutomationWorkflowRun") -> str | None:
        if getattr(run, "trigger_ref_type", None) != "appointment":
            return None
        appointment_id = getattr(run, "trigger_ref_id", None)
        if not appointment_id:
            return None
        try:
            return await self._check_appointment(run, str(appointment_id))
        except Exception as exc:  # noqa: BLE001 — fail-open on any error
            logger.warning(
                "revalidate: lookup failed run=%s appt=%s: %s — proceeding with send",
                getattr(run, "id", None), appointment_id, exc,
            )
            return None

    async def _check_appointment(
        self, run: "AutomationWorkflowRun", appointment_id: str
    ) -> str | None:
        from src.app.models.institution import Institution
        from src.app.models.institution_location import InstitutionLocation
        from src.app.pms.nexhealth.adapter import NexHealthAdapter

        if not run.location_id:
            return None
        location = await self._session.get(InstitutionLocation, run.location_id)
        institution = await self._session.get(Institution, run.institution_id)
        if location is None or institution is None:
            return None
        if not location.nexhealth_subdomain or not location.nexhealth_location_id:
            # Location not wired to NexHealth — cannot revalidate; fail open.
            return None

        adapter = await NexHealthAdapter.create(institution, location)
        try:
            appt = await adapter.get_appointment(appointment_id)
        finally:
            await adapter.close()

        if appt is None:
            # Could not read the appointment — fail open, do not drop the send.
            return None

        if bool(appt.get("cancelled", False) or appt.get("canceled", False)):
            return "skipped_cancelled"

        expected_at = (run.trigger_metadata or {}).get("appointment_at")
        current_at = appt.get("start_time")
        if expected_at and current_at and not _same_instant(expected_at, current_at):
            return "skipped_rescheduled"
        return None
