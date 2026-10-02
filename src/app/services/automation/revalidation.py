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

from sqlalchemy import select

from src.app.pms.gotracker.statuses import is_non_attending_status

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


def _gotracker_start_time(appointment: dict) -> str | None:
    """Normalize Tracker's split AppointmentDate/AppointmentTime response."""
    direct = appointment.get("start_time", appointment.get("StartTime"))
    if isinstance(direct, str) and direct.strip():
        return direct
    date = appointment.get("AppointmentDate", appointment.get("appointment_date"))
    time = appointment.get("AppointmentTime", appointment.get("appointment_time"))
    if not isinstance(date, str) or not isinstance(time, str):
        return None
    return f"{date.split('T', 1)[0]}T{time.split('T', 1)[-1].removesuffix('Z')}Z"


# A projection row synced within this window is trusted without a live NexHealth
# read (Plan 09 D-2 freshness window). Cuts the ~800-call burst when a large
# fixed-time batch all dispatches at once. Tunable.
_FRESHNESS_WINDOW_SECONDS = 900  # 15 minutes


class PmsLiveRevalidationService:
    """Plan 09 dispatch-time revalidator backed by the appointment working set +
    live NexHealth reads.

    Immediately before an appointment-triggered run sends, this checks the
    appointment's current status:

    * ``"skipped_cancelled"`` — the appointment was cancelled.
    * ``"skipped_rescheduled"`` — its start time no longer matches the time the
      run was enrolled against (``trigger_metadata['appointment_at']``).
    * ``None`` — still valid, proceed with the send.

    A recently-synced ``appointment_working_set`` row (within the freshness window)
    is trusted directly, avoiding a live NexHealth call per send (D-2). Only a
    missing/stale projection falls through to a live ``get_appointment``.

    Fail-open: any lookup/build error returns ``None`` so a transient NexHealth
    blip never drops a legitimate send (it is logged instead). Recall/manual
    runs carry no appointment ref and short-circuit to ``None``.
    """

    def __init__(self, session: "AsyncSession") -> None:
        self._session = session

    async def revalidate(self, run: "AutomationWorkflowRun") -> str | None:
        metadata = getattr(run, "trigger_metadata", None) or {}
        require_occurred = metadata.get("campaign_goal") == "post_op_followup"
        if require_occurred:
            deadline = _parse_dt(metadata.get("post_op_expires_at"))
            if deadline is not None and deadline <= datetime.now(timezone.utc):
                return "skipped_post_op_window_expired"

        appointment_id = (
            getattr(run, "trigger_ref_id", None)
            if getattr(run, "trigger_ref_type", None) == "appointment"
            else metadata.get("appointment_id")
        )
        if not appointment_id:
            if require_occurred:
                return "skipped_missing_appointment_context"
            return None
        try:
            async with self._session.begin_nested():
                return await self._check_appointment(
                    run,
                    str(appointment_id),
                    require_occurred=require_occurred,
                )
        except Exception as exc:  # noqa: BLE001 — fail-open on any error
            logger.warning(
                "revalidate: lookup failed run=%s appt=%s: %s — proceeding with send",
                getattr(run, "id", None),
                appointment_id,
                exc,
            )
            return None

    async def _check_appointment(
        self,
        run: "AutomationWorkflowRun",
        appointment_id: str,
        *,
        require_occurred: bool = False,
    ) -> str | None:
        from src.app.models.institution import Institution
        from src.app.models.institution_location import InstitutionLocation
        from src.app.pms.gotracker.adapter import GoTrackerAdapter
        from src.app.pms.nexhealth.adapter import NexHealthAdapter

        # Freshness window (D-2): trust a recently-synced projection row instead of
        # a live NexHealth read. Returns (decided, outcome); decided=False → stale
        # or missing, fall through to the live read below.
        decided, outcome = await self._check_projection(
            run,
            appointment_id,
            require_occurred=require_occurred,
        )
        if decided:
            return outcome

        if not run.location_id:
            return None
        location = await self._session.get(InstitutionLocation, run.location_id)
        institution = await self._session.get(Institution, run.institution_id)
        if location is None or institution is None:
            return None
        if getattr(institution, "pms_type", None) == "gotracker":
            adapter = await GoTrackerAdapter.create(institution, location)
            try:
                appt = await adapter.get_appointment(appointment_id)
            finally:
                await adapter.close()
            return self._gotracker_live_outcome(
                run,
                appt,
                require_occurred=require_occurred,
            )

        if await self._pms_read_unhealthy(run):
            logger.warning(
                "revalidate: PMS read sync unhealthy run=%s appt=%s — skipping send",
                getattr(run, "id", None),
                appointment_id,
            )
            return "skipped_pms_read_unhealthy"

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
        if require_occurred:
            current_dt = _parse_dt(current_at)
            if current_dt is None:
                current_dt = _parse_dt(expected_at)
            if current_dt is None:
                return "skipped_missing_appointment_context"
            if current_dt > datetime.now(timezone.utc):
                return "skipped_appointment_not_occurred"
        return None

    @staticmethod
    def _gotracker_live_outcome(
        run: "AutomationWorkflowRun",
        appt: dict | None,
        *,
        require_occurred: bool,
    ) -> str | None:
        """Apply GoTracker's raw appointment fields to the send-time guard."""
        if appt is None:
            return None  # unavailable / deleted rows fail open like NexHealth
        status_id = appt.get("StatusId", appt.get("status_id"))
        try:
            status_id = int(status_id) if status_id is not None else None
        except (TypeError, ValueError):
            status_id = None
        if (
            bool(appt.get("Cancelled", appt.get("cancelled", False)))
            or is_non_attending_status(status_id)
        ):
            return "skipped_cancelled"

        expected_at = (run.trigger_metadata or {}).get("appointment_at")
        current_at = _gotracker_start_time(appt)
        if expected_at and current_at and not _same_instant(expected_at, current_at):
            return "skipped_rescheduled"

        if require_occurred:
            expected_flow_state = (run.trigger_metadata or {}).get("flow_state")
            current_flow_state = appt.get("FlowState", appt.get("flow_state"))
            if (
                isinstance(current_flow_state, str)
                and isinstance(expected_flow_state, str)
                and current_flow_state.casefold() != expected_flow_state.casefold()
            ):
                return "skipped_appointment_not_completed"
            current_dt = _parse_dt(current_at) or _parse_dt(expected_at)
            if current_dt is None:
                return "skipped_missing_appointment_context"
            if current_dt > datetime.now(timezone.utc):
                return "skipped_appointment_not_occurred"
        return None

    async def _check_projection(
        self,
        run: "AutomationWorkflowRun",
        appointment_id: str,
        *,
        require_occurred: bool = False,
    ) -> tuple[bool, str | None]:
        """Decide from the working set if a fresh row exists.

        Returns ``(decided, outcome)``: ``decided=True`` means the projection was
        fresh enough to trust — ``outcome`` is the skip string or None (proceed).
        ``decided=False`` means missing/stale — the caller falls through to a
        live NexHealth read.
        """
        from src.app.models.appointment_working_set import AppointmentWorkingSet

        row = (
            await self._session.execute(
                select(AppointmentWorkingSet).where(
                    AppointmentWorkingSet.institution_id == run.institution_id,
                    AppointmentWorkingSet.nexhealth_appointment_id == appointment_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False, None

        # Tracker's non-attending dispositions are terminal for outreach. Trust
        # the last-known terminal state even if its normal freshness window has
        # elapsed: sending a reminder to a known no-show is worse than awaiting
        # a later webhook that reactivates the appointment.
        if row.status == "cancelled" or is_non_attending_status(
            getattr(row, "gotracker_status_id", None)
        ):
            return True, "skipped_cancelled"

        if row.last_synced_at is None:
            return False, None

        synced = row.last_synced_at
        if synced.tzinfo is None:
            synced = synced.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - synced).total_seconds()
        if age > _FRESHNESS_WINDOW_SECONDS:
            return False, None  # stale — revalidate live

        expected_at = (run.trigger_metadata or {}).get("appointment_at")
        if (
            expected_at
            and row.start_time
            and not _same_instant(expected_at, row.start_time.isoformat())
        ):
            return True, "skipped_rescheduled"
        if require_occurred:
            expected_flow_state = (run.trigger_metadata or {}).get("flow_state")
            current_flow_state = getattr(row, "flow_state", None)
            if (
                isinstance(current_flow_state, str)
                and isinstance(expected_flow_state, str)
                and current_flow_state.casefold() != expected_flow_state.casefold()
            ):
                return True, "skipped_appointment_not_completed"
            appointment_at = row.start_time or _parse_dt(expected_at)
            if appointment_at is None:
                return True, "skipped_missing_appointment_context"
            appointment_at = (
                appointment_at
                if appointment_at.tzinfo
                else appointment_at.replace(tzinfo=timezone.utc)
            )
            if appointment_at > datetime.now(timezone.utc):
                return True, "skipped_appointment_not_occurred"
        return True, None

    async def _pms_read_unhealthy(self, run: "AutomationWorkflowRun") -> bool:
        """True when latest sync-status says PMS reads are known unhealthy."""
        if not run.location_id:
            return False
        from src.app.models.nexhealth_sync_status import NexHealthSyncStatus
        from src.app.services.automation.nexhealth_sync_status_service import (
            assess_sync_status,
        )

        try:
            row = (
                await self._session.execute(
                    select(NexHealthSyncStatus).where(
                        NexHealthSyncStatus.institution_id == run.institution_id,
                        NexHealthSyncStatus.location_id == run.location_id,
                    )
                )
            ).scalar_one_or_none()
            return assess_sync_status(row).read_healthy is False
        except Exception as exc:  # noqa: BLE001 — fail-open on malformed health state
            logger.warning(
                "revalidate: PMS sync-status lookup failed run=%s: %s",
                getattr(run, "id", None),
                exc,
            )
            return False
