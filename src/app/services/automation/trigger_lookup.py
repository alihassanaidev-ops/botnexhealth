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


async def find_active_workflows(
    session: AsyncSession,
    *,
    institution_id: str,
    trigger_type: str,
    location_id: str | None,
) -> list[AutomationWorkflow]:
    """Active workflows for one trigger type that are in scope for this location.

    ``trigger_type`` is read from the current version's definition JSON rather
    than a column, so the filter stays in Python. Phase 4 replaces this with an
    indexed subscription table; the contract of this function does not change.
    """
    result = await session.execute(active_workflows_stmt(institution_id))
    return [
        workflow
        for workflow in result.scalars().all()
        if workflow.trigger_type == trigger_type
        and workflow_matches_location(workflow, location_id)
    ]
