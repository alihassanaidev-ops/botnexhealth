"""Trigger matching for workflows that start from patient status events."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow
from src.app.services.automation.definition_schema import (
    InternalStatusTrigger,
    WorkflowDefinition,
)
from src.app.services.automation.trigger_lookup import find_active_workflows


class PatientStatusTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_status_workflows(
        self, institution_id: str, *, location_id: str | None = None
    ) -> list[AutomationWorkflow]:
        """Return in-scope active workflows started by an internal status change."""
        return await find_active_workflows(
            self.session,
            institution_id=institution_id,
            trigger_type="internal_status",
            location_id=location_id,
        )


def workflow_matches_patient_status(
    workflow: AutomationWorkflow,
    status: str,
    *,
    field: str = "patient_workflow_status",
    previous_status: str | None = None,
) -> bool:
    """True if ``workflow`` should enroll for this status transition.

    ``from_statuses`` empty means "arrived at one of ``to_statuses`` from
    anything", which is what most campaigns want; naming it restricts the
    trigger to a specific transition.
    """
    if not workflow.definition:
        return False
    try:
        definition = WorkflowDefinition.model_validate(workflow.definition)
    except Exception:
        return False

    folded = status.casefold()
    for trigger in definition.triggers:
        if not isinstance(trigger, InternalStatusTrigger):
            continue
        if trigger.field != field:
            continue
        if folded not in {value.casefold() for value in trigger.to_statuses}:
            continue
        if trigger.from_statuses:
            if previous_status is None:
                continue
            if previous_status.casefold() not in {
                value.casefold() for value in trigger.from_statuses
            }:
                continue
        return True
    return False


def patient_status_idempotency_key(
    workflow_version_id: str,
    status_event_id: str,
) -> str:
    """Stable dedupe key: one downstream run per status event per version."""
    return f"patient-status:{workflow_version_id}:{status_event_id}"

