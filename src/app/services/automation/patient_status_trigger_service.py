"""Trigger matching for workflows that start from patient status events."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow, AutomationWorkflowStatus
from src.app.services.automation.definition_schema import (
    PatientStatusChangedTrigger,
    WorkflowDefinition,
)


class PatientStatusTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_status_workflows(
        self, institution_id: str
    ) -> list[AutomationWorkflow]:
        """Return active workflows whose trigger type is 'patient_status_changed'."""
        result = await self.session.execute(
            select(AutomationWorkflow).where(
                AutomationWorkflow.institution_id == institution_id,
                AutomationWorkflow.status == AutomationWorkflowStatus.ACTIVE.value,
                AutomationWorkflow.current_version_id.is_not(None),
            )
        )
        return [
            wf for wf in result.scalars().all()
            if wf.trigger_type == "patient_status_changed"
        ]


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

