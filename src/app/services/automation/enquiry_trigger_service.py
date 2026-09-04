"""Match landed sales enquiries to active workflow triggers.

Item 24 starts from the intake pipeline, not from a scheduler sweep. The intake
route owns writing/deduplicating the Contact; this service owns the workflow side
of the boundary: find active ``enquiry_received`` workflows, apply trigger
filters, and produce PHI-light enrollment task payloads.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow
from src.app.models.contact import Contact
from src.app.services.automation.definition_schema import (
    EventTrigger,
    WorkflowDefinition,
)
from src.app.services.automation.trigger_filter import trigger_filter_matches
from src.app.services.automation.trigger_lookup import find_active_workflows


@dataclass(frozen=True)
class EnquiryWorkflowDispatch:
    """A Celery enrollment payload for one matching enquiry workflow."""

    institution_id: str
    workflow_id: str
    workflow_version_id: str
    contact_id: str
    location_id: str | None
    trigger_ref_id: str
    idempotency_key: str
    trigger_metadata: dict[str, Any]

    def task_kwargs(self) -> dict[str, Any]:
        return {
            "institution_id": self.institution_id,
            "workflow_id": self.workflow_id,
            "workflow_version_id": self.workflow_version_id,
            "contact_id": self.contact_id,
            "location_id": self.location_id,
            "trigger_type": "enquiry_received",
            "trigger_ref_type": "contact",
            "trigger_ref_id": self.trigger_ref_id,
            "idempotency_key": self.idempotency_key,
            "trigger_metadata": self.trigger_metadata,
        }


class EnquiryTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_enquiry_workflows(
        self, institution_id: str, *, location_id: str | None = None
    ) -> list[AutomationWorkflow]:
        """Return in-scope active workflows triggered by ``enquiry_received``."""
        return await find_active_workflows(
            self.session,
            institution_id=institution_id,
            trigger_type="enquiry_received",
            location_id=location_id,
        )

    async def prepare_dispatches(
        self,
        *,
        institution_id: str,
        location_id: str | None,
        contact: Contact,
        intake_key: str | None,
        source: str,
        created: bool,
        matched_existing_contact: bool,
    ) -> list[EnquiryWorkflowDispatch]:
        """Build enrollment task payloads for every matching active workflow."""
        contact_id = _as_str(getattr(contact, "id", None))
        if not contact_id:
            return []

        base_context = enquiry_trigger_context(
            contact=contact,
            location_id=location_id,
            intake_key=intake_key,
            source=source,
            created=created,
            matched_existing_contact=matched_existing_contact,
        )
        workflows = await self.find_active_enquiry_workflows(
            institution_id, location_id=location_id
        )

        dispatches: list[EnquiryWorkflowDispatch] = []
        for workflow in workflows:
            version_id = _as_str(getattr(workflow, "current_version_id", None))
            if not version_id:
                continue
            run_location_id = (
                _as_str(getattr(workflow, "location_id", None)) or location_id
            )
            workflow_context = _with_effective_location(base_context, run_location_id)
            if not workflow_matches_enquiry(workflow, workflow_context):
                continue

            dispatches.append(
                EnquiryWorkflowDispatch(
                    institution_id=institution_id,
                    workflow_id=str(workflow.id),
                    workflow_version_id=version_id,
                    contact_id=contact_id,
                    location_id=run_location_id,
                    trigger_ref_id=contact_id,
                    idempotency_key=make_enquiry_idempotency_key(
                        version_id,
                        contact_id,
                        intake_key=intake_key,
                    ),
                    trigger_metadata=workflow_context,
                )
            )
        return dispatches


def enquiry_trigger_context(
    *,
    contact: Contact,
    location_id: str | None,
    intake_key: str | None,
    source: str,
    created: bool,
    matched_existing_contact: bool,
) -> dict[str, Any]:
    """Normalized PHI-light context carried into an enquiry workflow run."""
    contact_id = _as_str(getattr(contact, "id", None))
    contact_location_id = _as_str(getattr(contact, "location_id", None))
    effective_location_id = location_id or contact_location_id
    lead_source = source or getattr(contact, "lead_source", None) or "unknown"
    lead_status = getattr(contact, "lead_status", None)
    external_ref = getattr(contact, "external_ref", None)
    patient_id = getattr(contact, "nexhealth_patient_id", None)

    context: dict[str, Any] = {
        "event": "enquiry.received",
        "trigger_type": "enquiry_received",
        "contact_id": contact_id,
        "enquiry_id": contact_id,
        "enquiry_contact_id": contact_id,
        "location_id": effective_location_id,
        "enquiry_source": lead_source,
        "enquiry_status": lead_status,
        "enquiry_intake_key": intake_key,
        "enquiry_external_ref": external_ref,
        "enquiry_created": bool(created),
        "matched_existing_contact": bool(matched_existing_contact),
        "patient_id": patient_id,
        "nexhealth_patient_id": patient_id,
        "enquiry": {
            "id": contact_id,
            "contact_id": contact_id,
            "source": lead_source,
            "status": lead_status,
            "intake_key": intake_key,
            "external_ref": external_ref,
            "created": bool(created),
            "matched_existing_contact": bool(matched_existing_contact),
            "location_id": effective_location_id,
        },
    }
    return _strip_none(context)


def workflow_matches_enquiry(
    workflow: AutomationWorkflow,
    context: Mapping[str, Any],
) -> bool:
    """Return whether a workflow definition admits this landed enquiry."""
    if not workflow.definition:
        return False
    try:
        definition = WorkflowDefinition.model_validate(workflow.definition)
    except Exception:
        return False
    subscribed = any(
        isinstance(trigger, EventTrigger) and "enquiry.received" in trigger.event_keys
        for trigger in definition.triggers
    )
    if not subscribed:
        return False
    return trigger_filter_matches(workflow, context)


def make_enquiry_idempotency_key(
    workflow_version_id: str,
    contact_id: str,
    *,
    intake_key: str | None = None,
) -> str:
    """Stable key for one intake submission per contact per workflow version."""
    grain = (intake_key or contact_id).strip() or contact_id
    digest = hashlib.sha256(grain.encode("utf-8")).hexdigest()[:24]
    return f"enquiry:{workflow_version_id}:{contact_id}:{digest}"


def enqueue_enquiry_workflow_dispatches(
    dispatches: list[EnquiryWorkflowDispatch],
) -> int:
    """Enqueue prepared dispatches after the intake transaction has committed."""
    if not dispatches:
        return 0

    from src.app.tasks.automation_workflow import enroll_and_start_workflow_run

    count = 0
    for dispatch in dispatches:
        enroll_and_start_workflow_run.apply_async(
            kwargs=dispatch.task_kwargs(),
            queue="workflow",
        )
        count += 1
    return count


def _with_effective_location(
    context: dict[str, Any],
    location_id: str | None,
) -> dict[str, Any]:
    scoped = dict(context)
    if location_id:
        scoped["location_id"] = location_id
        enquiry = scoped.get("enquiry")
        if isinstance(enquiry, dict):
            scoped["enquiry"] = {**enquiry, "location_id": location_id}
    return scoped


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_none(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, list):
        return [_strip_none(item) for item in value]
    return value


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
