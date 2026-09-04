"""Service for matching appointment events to AppointmentOffsetTrigger workflows.

Called by the appointment Celery task. Does not make NexHealth API calls —
it only queries our own DB for active workflows that match the trigger type
and computes the enrollment ETA from the appointment time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.appointment_working_set import AppointmentWorkingSet
from src.app.models.automation_workflow import AutomationWorkflow
from src.app.models.institution_appointment_type import InstitutionAppointmentType
from src.app.pms.gotracker.statuses import status_for_id
from src.app.services.automation.definition_schema import (
    EventTrigger,
    PmsRecallSource,
    ScheduleTrigger,
    WorkflowDefinition,
)

#: PMS-neutral appointment status → the event that status change represents.
_STATE_EVENT_BY_SEMANTICS: dict[str, str] = {
    "cancelled": "appointment.cancelled",
    "no_show": "appointment.no_show",
    "waiting": "appointment.checked_in",
}
from src.app.services.automation.trigger_filter import trigger_filter_matches
from src.app.services.automation.trigger_lookup import find_active_workflows


class AppointmentTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_appointment_workflows(
        self, institution_id: str, *, location_id: str | None = None
    ) -> list[AutomationWorkflow]:
        """Return in-scope active workflows triggered by 'appointment_offset'."""
        return await find_active_workflows(
            self.session,
            institution_id=institution_id,
            trigger_type="appointment_offset",
            location_id=location_id,
        )

    async def get_appointment_context(
        self,
        *,
        institution_id: str,
        appointment_id: str,
        fallback_location_id: str | None = None,
    ) -> dict:
        """Return normalized workflow context for a local appointment projection."""
        result = await self.session.execute(
            select(AppointmentWorkingSet).where(
                AppointmentWorkingSet.institution_id == institution_id,
                AppointmentWorkingSet.nexhealth_appointment_id == appointment_id,
            )
        )
        appt = result.scalar_one_or_none()
        if appt is None:
            return {}

        type_name = None
        if appt.appointment_type_id:
            type_result = await self.session.execute(
                select(InstitutionAppointmentType).where(
                    InstitutionAppointmentType.institution_id == institution_id,
                    InstitutionAppointmentType.source_id == appt.appointment_type_id,
                    InstitutionAppointmentType.location_id
                    == (appt.location_id or fallback_location_id),
                )
            )
            appt_type = type_result.scalar_one_or_none()
            if appt_type is not None:
                type_name = appt_type.name

        return {
            "appointment_id": appt.nexhealth_appointment_id,
            "appointment_at": appt.start_time.isoformat() if appt.start_time else None,
            "appointment_start_time": appt.start_time.isoformat()
            if appt.start_time
            else None,
            "appointment_status": appt.status,
            "appointment_reason": appt.appointment_reason,
            "appointment_type_id": appt.appointment_type_id,
            "appointment_type": type_name or appt.appointment_type_id,
            "appointment_type_name": type_name,
            "provider_id": appt.provider_id,
            "patient_id": appt.nexhealth_patient_id,
            "contact_id": appt.contact_id,
            "location_id": appt.location_id or fallback_location_id,
            "appointment": {
                "id": appt.nexhealth_appointment_id,
                "start_time": appt.start_time.isoformat() if appt.start_time else None,
                "status": appt.status,
                "reason": appt.appointment_reason,
                "appointment_type_id": appt.appointment_type_id,
                "appointment_type_name": type_name,
                "provider_id": appt.provider_id,
            },
        }

    async def find_active_recall_workflows(
        self, institution_id: str, *, location_id: str | None = None
    ) -> list[AutomationWorkflow]:
        """Return in-scope active workflows triggered by 'recall_scan'."""
        return await find_active_workflows(
            self.session,
            institution_id=institution_id,
            trigger_type="recall_scan",
            location_id=location_id,
        )

    async def find_active_appointment_state_workflows(
        self, institution_id: str, *, location_id: str | None = None
    ) -> list[AutomationWorkflow]:
        """Return in-scope active workflows triggered by 'appointment_state_changed'."""
        return await find_active_workflows(
            self.session,
            institution_id=institution_id,
            trigger_type="appointment_state_changed",
            location_id=location_id,
        )


def _validated_definition(workflow: AutomationWorkflow) -> WorkflowDefinition | None:
    if not workflow.definition:
        return None
    try:
        return WorkflowDefinition.model_validate(workflow.definition)
    except Exception:
        return None


def _event_trigger_for(
    defn: WorkflowDefinition, event_key: str
) -> EventTrigger | None:
    """The workflow's event trigger subscribed to ``event_key``, if any."""
    for trigger in defn.triggers:
        if isinstance(trigger, EventTrigger) and event_key in trigger.event_keys:
            return trigger
    return None


def compute_enrollment_eta(
    workflow: AutomationWorkflow, appointment_at: datetime
) -> datetime | None:
    """Return the UTC datetime at which to enroll, or None if the window passed.

    Only ``appointment.reminder_due`` carries an interval — it is the event whose
    whole meaning is "a configured time relative to the appointment". Every other
    appointment event fires when it happens, and a campaign that wants to wait
    puts a wait node after the trigger.
    """
    defn = _validated_definition(workflow)
    if defn is None:
        return None

    trigger = _event_trigger_for(defn, "appointment.reminder_due")
    if trigger is None or trigger.reminder_offset_hours is None:
        return None

    enrollment_eta = appointment_at + timedelta(hours=trigger.reminder_offset_hours)
    now = datetime.now(tz=timezone.utc)
    if enrollment_eta <= now:
        return None

    return enrollment_eta


def workflow_matches_appointment(
    workflow: AutomationWorkflow,
    *,
    appointment_type_id: str | None = None,
    appointment_type_name: str | None = None,
) -> bool:
    """Whether a reminder workflow should receive this appointment.

    Appointment type selection used to happen here through the trigger's
    ``appointment_type_ids`` field. Definitions express it as a trigger filter on
    canonical context instead, because GoTracker sends appointment reasons rather
    than our local appointment type ids. The unused parameters remain for
    compatibility with older call sites.
    """
    _ = (appointment_type_id, appointment_type_name)
    defn = _validated_definition(workflow)
    if defn is None:
        return False
    return _event_trigger_for(defn, "appointment.reminder_due") is not None


def appointment_state_event_key(
    *,
    status_id: int | None = None,
    confirmed: bool | None = None,
    flow_state: str | None = None,
) -> str | None:
    """Canonical event key for a cached appointment-state change.

    The state matchers a workflow used to spell out — status ids, Chair Flow
    strings, a confirmed flag — collapse into the event that state *means*, so a
    campaign written once matches on either practice-management system.
    """
    if flow_state and flow_state.strip().casefold() == "completed":
        return "appointment.completed"
    if status_id is not None:
        status = status_for_id(status_id)
        semantics = getattr(status, "semantics", None)
        mapped = _STATE_EVENT_BY_SEMANTICS.get(semantics or "")
        if mapped:
            return mapped
    if confirmed:
        return "appointment.confirmed"
    return None


def workflow_matches_appointment_state(
    workflow: AutomationWorkflow,
    *,
    status_id: int | None = None,
    confirmed: bool | None = None,
    preconfirmed: bool | None = None,
    flow_state: str | None = None,
) -> bool:
    _ = preconfirmed  # no canonical event; a filter on raw.* expresses it
    defn = _validated_definition(workflow)
    if defn is None:
        return False

    event_key = appointment_state_event_key(
        status_id=status_id, confirmed=confirmed, flow_state=flow_state
    )
    if event_key is None:
        return False
    return _event_trigger_for(defn, event_key) is not None


def workflow_matches_recall(
    workflow: AutomationWorkflow,
    context: dict,
    *,
    location_timezone: str = "UTC",
) -> bool:
    """Whether a scheduled recall workflow should receive this recall row."""
    defn = _validated_definition(workflow)
    if defn is None:
        return False

    sourced = any(
        isinstance(trigger, ScheduleTrigger)
        and isinstance(trigger.source, PmsRecallSource)
        for trigger in defn.triggers
    )
    if not sourced:
        return False

    return trigger_filter_matches(
        workflow,
        context,
        location_timezone=location_timezone,
    )


def make_appointment_state_idempotency_key(
    workflow_version_id: str,
    appointment_id: str,
    *,
    status_id: int | None = None,
    confirmed: bool | None = None,
    preconfirmed: bool | None = None,
    flow_state: str | None = None,
    flow_changed_at: str | None = None,
) -> str:
    key = (
        f"appt-state:{workflow_version_id}:{appointment_id}:"
        f"status={status_id}:confirmed={confirmed}:preconfirmed={preconfirmed}"
    )
    # Preserve keys for existing status/confirmation workflows. A flow event
    # needs its own timestamped key: the same appointment can legitimately be
    # completed again only after a later, distinct FlowChange.
    if flow_state is None and flow_changed_at is None:
        return key
    return f"{key}:flow_state={flow_state}:flow_changed_at={flow_changed_at}"


def make_appointment_idempotency_key(
    workflow_version_id: str,
    appointment_id: str,
    appointment_at_iso: str | None = None,
) -> str:
    """Idempotency key for one appointment enrollment per version.

    The key is **time-aware** (Plan 09 D-1): including the normalized start
    instant means a *reschedule* to a new time produces a NEW key, so the
    re-enroll is not deduped against the (now-cancelled) run for the old time.
    Redeliveries at the *same* time normalise to the same key and still dedupe.
    Falls back to the time-independent key when no start time is available.
    """
    if not appointment_at_iso:
        return f"appt:{workflow_version_id}:{appointment_id}"
    dt = _parse_instant(appointment_at_iso)
    stamp = dt.isoformat() if dt else appointment_at_iso
    return f"appt:{workflow_version_id}:{appointment_id}:{stamp}"


def _parse_instant(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(
        timezone.utc
    )


def make_recall_idempotency_key(
    workflow_version_id: str, patient_id: str, period: str
) -> str:
    """Stable idempotency key for recall enrollment.

    Scoped by ``period`` (e.g. ``"2026-07"``) so a patient who stays overdue is
    enrolled at most once per period per workflow version, even though the recall
    scanner runs repeatedly (hourly beat).
    """
    return f"recall:{workflow_version_id}:{patient_id}:{period}"
