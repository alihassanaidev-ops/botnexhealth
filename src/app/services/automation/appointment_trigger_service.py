"""Service for matching appointment events to AppointmentOffsetTrigger workflows.

Called by the appointment Celery task. Does not make NexHealth API calls —
it only queries our own DB for active workflows that match the trigger type
and computes the enrollment ETA from the appointment time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow, AutomationWorkflowStatus
from src.app.services.automation.definition_schema import (
    AppointmentOffsetTrigger,
    WorkflowDefinition,
)


class AppointmentTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_appointment_workflows(
        self, institution_id: str
    ) -> list[AutomationWorkflow]:
        """Return active workflows whose definition trigger type is 'appointment_offset'."""
        result = await self.session.execute(
            select(AutomationWorkflow).where(
                AutomationWorkflow.institution_id == institution_id,
                AutomationWorkflow.status == AutomationWorkflowStatus.ACTIVE.value,
                AutomationWorkflow.current_version_id.is_not(None),
            )
        )
        return [
            wf for wf in result.scalars().all()
            if wf.trigger_type == "appointment_offset"
        ]

    async def find_active_recall_workflows(
        self, institution_id: str
    ) -> list[AutomationWorkflow]:
        """Return active workflows whose definition trigger type is 'recall_scan'."""
        result = await self.session.execute(
            select(AutomationWorkflow).where(
                AutomationWorkflow.institution_id == institution_id,
                AutomationWorkflow.status == AutomationWorkflowStatus.ACTIVE.value,
                AutomationWorkflow.current_version_id.is_not(None),
            )
        )
        return [
            wf for wf in result.scalars().all()
            if wf.trigger_type == "recall_scan"
        ]


def compute_enrollment_eta(
    workflow: AutomationWorkflow, appointment_at: datetime
) -> datetime | None:
    """Return the UTC datetime at which to enroll, or None if the window has passed.

    Parses the trigger from the workflow's current definition to extract
    offset_hours, then computes appointment_at + offset_hours.
    If the result is already in the past, returns None (skip enrollment).
    """
    if not workflow.definition:
        return None

    try:
        defn = WorkflowDefinition.model_validate(workflow.definition)
    except Exception:
        return None

    if not isinstance(defn.trigger, AppointmentOffsetTrigger):
        return None

    enrollment_eta = appointment_at + timedelta(hours=defn.trigger.offset_hours)
    now = datetime.now(tz=timezone.utc)
    if enrollment_eta <= now:
        return None

    return enrollment_eta


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
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def make_recall_idempotency_key(
    workflow_version_id: str, patient_id: str, period: str
) -> str:
    """Stable idempotency key for recall enrollment.

    Scoped by ``period`` (e.g. ``"2026-07"``) so a patient who stays overdue is
    enrolled at most once per period per workflow version, even though the recall
    scanner runs repeatedly (hourly beat).
    """
    return f"recall:{workflow_version_id}:{patient_id}:{period}"
