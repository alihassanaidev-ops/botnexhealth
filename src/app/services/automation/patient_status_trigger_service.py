"""Trigger matching for workflows that start from patient status events."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow
from src.app.services.automation.definition_schema import (
    PatientStatusChangedTrigger,
    WorkflowDefinition,
)
from src.app.services.automation.trigger_lookup import find_active_workflows


class PatientStatusTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_status_workflows(
        self, institution_id: str, *, location_id: str | None = None
    ) -> list[AutomationWorkflow]:
        """Return in-scope active workflows triggered by 'patient_status_changed'."""
        return await find_active_workflows(
            self.session,
            institution_id=institution_id,
            trigger_type="patient_status_changed",
            location_id=location_id,
        )


def workflow_matches_patient_status(workflow: AutomationWorkflow, status: str) -> bool:
    """True if ``workflow`` should enroll for this status event."""
    if not workflow.definition:
        return False
    try:
        definition = WorkflowDefinition.model_validate(workflow.definition)
    except Exception:
        return False
    trigger = definition.trigger
    if not isinstance(trigger, PatientStatusChangedTrigger):
        return False
    return status in trigger.statuses


def patient_status_idempotency_key(
    workflow_version_id: str,
    status_event_id: str,
) -> str:
    """Stable dedupe key: one downstream run per status event per version."""
    return f"patient-status:{workflow_version_id}:{status_event_id}"

