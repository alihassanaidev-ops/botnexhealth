"""Match a landed form submission to the workflows waiting for it.

Same shape as every other trigger service: find the active workflows for this
trigger type in this location, decide which ones this event admits, and produce
enrollment payloads for the ones that do. The decision has one extra step here —
a ``form_submitted`` trigger can name a provider and specific forms, and that
selection is checked before the generic eligibility filter runs.

Which forms a workflow names is checked against *our* form ids, never the
provider's. A provider's form id is unique only within that provider's account,
so matching on it would let one clinic's definition name a form belonging to
somebody else who happens to be connected to the same provider.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow
from src.app.services.automation.definition_schema import (
    FormSubmittedTrigger,
    WorkflowDefinition,
)
from src.app.services.automation.trigger_filter import trigger_filter_matches
from src.app.services.automation.trigger_lookup import find_active_workflows

TRIGGER_TYPE = "form_submitted"


@dataclass(frozen=True)
class FormWorkflowDispatch:
    """A Celery enrollment payload for one matching form workflow."""

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
            "trigger_type": TRIGGER_TYPE,
            # The submission, not the contact: the same person submitting the
            # same form twice is two events and should enroll twice, while one
            # submission redelivered is one event and must not.
            "trigger_ref_type": "form_submission",
            "trigger_ref_id": self.trigger_ref_id,
            "idempotency_key": self.idempotency_key,
            "trigger_metadata": self.trigger_metadata,
        }


class FormTriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_active_form_workflows(
        self, institution_id: str, *, location_id: str | None = None
    ) -> list[AutomationWorkflow]:
        return await find_active_workflows(
            self.session,
            institution_id=institution_id,
            trigger_type=TRIGGER_TYPE,
            location_id=location_id,
        )

    async def prepare_dispatches(
        self,
        *,
        institution_id: str,
        location_id: str | None,
        contact_id: str,
        submission_id: str,
        context: Mapping[str, Any],
    ) -> list[FormWorkflowDispatch]:
        workflows = await self.find_active_form_workflows(
            institution_id, location_id=location_id
        )

        dispatches: list[FormWorkflowDispatch] = []
        for workflow in workflows:
            version_id = _as_str(getattr(workflow, "current_version_id", None))
            if not version_id:
                continue
            run_location_id = (
                _as_str(getattr(workflow, "location_id", None)) or location_id
            )
            workflow_context = _with_effective_location(dict(context), run_location_id)
            if not workflow_matches_submission(workflow, workflow_context):
                continue

            dispatches.append(
                FormWorkflowDispatch(
                    institution_id=institution_id,
                    workflow_id=str(workflow.id),
                    workflow_version_id=version_id,
                    contact_id=contact_id,
                    location_id=run_location_id,
                    trigger_ref_id=submission_id,
                    idempotency_key=make_form_idempotency_key(
                        version_id, submission_id
                    ),
                    trigger_metadata=workflow_context,
                )
            )
        return dispatches


def workflow_matches_submission(
    workflow: AutomationWorkflow, context: Mapping[str, Any]
) -> bool:
    """Whether this workflow's trigger admits this submission."""
    if not workflow.definition:
        return False
    try:
        definition = WorkflowDefinition.model_validate(workflow.definition)
    except Exception:  # noqa: BLE001 — an unparseable definition matches nothing
        return False
    trigger = definition.trigger
    if not isinstance(trigger, FormSubmittedTrigger):
        return False

    if trigger.provider and trigger.provider != context.get("form_provider"):
        return False
    # Empty means every enabled form, which is what a clinic running one form
    # wants and what a template shipped before any form existed has to mean.
    if trigger.form_ids and str(context.get("form_id") or "") not in set(
        trigger.form_ids
    ):
        return False

    return trigger_filter_matches(workflow, context)


def make_form_idempotency_key(workflow_version_id: str, submission_id: str) -> str:
    """One run per submission per workflow version."""
    digest = hashlib.sha256(submission_id.encode("utf-8")).hexdigest()[:24]
    return f"form:{workflow_version_id}:{digest}"


def enqueue_form_workflow_dispatches(
    dispatches: list[FormWorkflowDispatch],
) -> int:
    """Enqueue prepared dispatches after the webhook transaction has committed."""
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
    context: dict[str, Any], location_id: str | None
) -> dict[str, Any]:
    if not location_id:
        return context
    scoped = dict(context)
    scoped["location_id"] = location_id
    return scoped


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
