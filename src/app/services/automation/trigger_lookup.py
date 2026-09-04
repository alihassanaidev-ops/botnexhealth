"""Shared active-workflow lookup for every trigger service.

Trigger matching used to be copy-pasted per trigger type, and each copy decided
independently whether to honour ``AutomationWorkflow.location_id``. Only the SMS
reply path did, so an event at one location could enroll a workflow scoped to a
different location in the same institution — the run then carried the *event's*
location, so the patient was contacted with the wrong clinic's voice profile,
sending number and hours.

The scoping rule lives here once:

* ``location_id IS NULL`` on the workflow means institution-wide — it matches any
  location, which is what unscoped workflows have always done.
* A workflow bound to a location matches only that location. An event with no
  resolvable location therefore matches institution-wide workflows only, because
  we cannot prove it belongs to the bound location.
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.models.automation_workflow import (
    AutomationWorkflow,
    AutomationWorkflowStatus,
)


def active_workflows_stmt(institution_id: str) -> Select[tuple[AutomationWorkflow]]:
    """Select every published, active workflow for one institution."""
    return (
        select(AutomationWorkflow)
        .options(selectinload(AutomationWorkflow.current_version))
        .where(
            AutomationWorkflow.institution_id == institution_id,
            AutomationWorkflow.status == AutomationWorkflowStatus.ACTIVE.value,
            AutomationWorkflow.current_version_id.is_not(None),
        )
    )


def workflow_matches_location(
    workflow: AutomationWorkflow, location_id: str | None
) -> bool:
    """Whether a workflow may run for an event at ``location_id``."""
    if workflow.location_id is None:
        return True
    return str(workflow.location_id) == str(location_id) if location_id else False


#: What each trigger type is really watching, in canonical event terms.
#:
#: This is the bridge that lets the existing per-type dispatch tasks find an
#: `event`-authored workflow without every publisher being rewritten first: a
#: task that asks for ``"appointment_offset"`` also matches a workflow
#: subscribed to ``appointment.reminder_due``.
TRIGGER_EVENT_KEYS: dict[str, tuple[str, ...]] = {
    # Retired types, still named by the dispatch tasks that have not moved yet.
    "appointment_offset": ("appointment.reminder_due",),
    "appointment_state_changed": (
        "appointment.completed",
        "appointment.cancelled",
        "appointment.confirmed",
        "appointment.no_show",
        "appointment.checked_in",
    ),
    "recall_scan": ("patient.recall_due",),
    "enquiry_received": ("enquiry.received",),
    "callback_requested": ("call.inbound.completed",),
    "patient_status_changed": ("patient.status_changed",),
    "sms_reply": ("message.sms.inbound",),
    "email_reply": ("message.email.inbound",),
    # Current types.
    "internal_status": ("patient.status_changed",),
    "inbound_message": ("message.sms.inbound", "message.email.inbound"),
    "schedule": ("patient.recall_due", "schedule.tick"),
}


def workflow_starts_from(workflow: AutomationWorkflow, trigger_type: str) -> bool:
    """Whether ``workflow`` should react to what ``trigger_type`` represents.

    A direct type match wins. Otherwise the workflow matches when the events it
    subscribes to overlap the events that trigger type raises — which is how an
    `event` trigger is found by a dispatch task that still speaks the old
    vocabulary.
    """
    if trigger_type in workflow.trigger_types:
        return True
    produced = TRIGGER_EVENT_KEYS.get(trigger_type)
    if not produced:
        return False
    subscribed = set(workflow.subscribed_event_keys)
    return any(key in subscribed for key in produced)


async def find_active_workflows(
    session: AsyncSession,
    *,
    institution_id: str,
    trigger_type: str,
    location_id: str | None,
) -> list[AutomationWorkflow]:
    """Active workflows for one trigger type that are in scope for this location.

    Triggers are read from the current version's definition JSON rather than a
    column, so the filter stays in Python. An indexed subscription table would
    narrow the candidate set, but it could never decide what a workflow's
    trigger actually is — that stays here.
    """
    result = await session.execute(active_workflows_stmt(institution_id))
    return [
        workflow
        for workflow in result.scalars().all()
        if workflow_starts_from(workflow, trigger_type)
        and workflow_matches_location(workflow, location_id)
    ]
