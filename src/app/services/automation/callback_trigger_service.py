"""Service for matching needs_callback classifications to CallbackRequestedTrigger workflows (Plan 07).

An inbound call classified `needs_callback` can be handled automatically by AI:
if the clinic has an active workflow whose trigger type is `callback_requested`,
the contact is enrolled into it and the outbound Retell agent (Plan 03) calls the
patient back. With no such active workflow, callbacks stay in the manual queue
(today's default behavior) — opt-in is via activating the workflow, no separate flag.

Mirrors AppointmentTriggerService: this only queries our own DB; the Celery task
(`trigger_callback_workflows`) schedules the enrollment at the requested time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow, AutomationWorkflowStatus


class CallbackTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_callback_workflows(
        self, institution_id: str
    ) -> list[AutomationWorkflow]:
        """Return active workflows whose definition trigger type is 'callback_requested'."""
        result = await self.session.execute(
            select(AutomationWorkflow).where(
                AutomationWorkflow.institution_id == institution_id,
                AutomationWorkflow.status == AutomationWorkflowStatus.ACTIVE.value,
                AutomationWorkflow.current_version_id.is_not(None),
            )
        )
        return [
            wf for wf in result.scalars().all()
            if wf.trigger_type == "callback_requested"
        ]


def compute_callback_eta(
    preferred_callback_at: datetime | None, now: datetime
) -> datetime | None:
    """Return the ETA at which to place the callback, or None to enroll immediately.

    Honors the patient's requested callback time when it is in the future;
    otherwise (no requested time, or a time already passed) returns None so the
    caller enrolls right away. Quiet-hours handling is left to the compliance gate
    at dispatch time: a time landing outside the clinic's operating hours is held,
    leaving the call in the manual queue rather than dialing after hours.
    """
    if preferred_callback_at is None:
        return None
    if preferred_callback_at.tzinfo is None:
        return None
    if preferred_callback_at <= now:
        return None
    return preferred_callback_at


def make_callback_idempotency_key(workflow_version_id: str, call_id: str) -> str:
    """Stable idempotency key preventing double-enrollment per call per version."""
    return f"callback:{workflow_version_id}:{call_id}"
