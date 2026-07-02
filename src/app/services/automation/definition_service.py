"""Workflow definition lifecycle: create draft, publish version, pause, archive."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import HTTPException, status as http_status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import (
    AutomationWorkflow,
    AutomationWorkflowStatus,
    AutomationWorkflowVersion,
)
from src.app.services.automation.definition_schema import WorkflowDefinition

logger = logging.getLogger(__name__)

_EDITABLE_STATUSES = {AutomationWorkflowStatus.DRAFT.value}
_PUBLISHABLE_STATUSES = {
    AutomationWorkflowStatus.DRAFT.value,
    AutomationWorkflowStatus.PAUSED.value,
}


class AutomationWorkflowDefinitionService:
    """Manages workflow definition lifecycle: CRUD, versioning, publish, pause, archive."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_draft(
        self,
        institution_id: str,
        *,
        name: str,
        location_id: str | None = None,
        description: str | None = None,
        category: str | None = None,
        is_template: bool = False,
        created_by_user_id: str | None = None,
    ) -> AutomationWorkflow:
        name = name.strip()
        if not name:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST, detail="name is required"
            )
        workflow = AutomationWorkflow(
            institution_id=institution_id,
            location_id=location_id,
            name=name,
            description=description,
            category=category,
            is_template=is_template,
            created_by_user_id=created_by_user_id,
            status=AutomationWorkflowStatus.DRAFT.value,
        )
        self.session.add(workflow)
        await self.session.flush()
        return workflow

    async def get_workflow(
        self, institution_id: str, workflow_id: str
    ) -> AutomationWorkflow | None:
        result = await self.session.execute(
            select(AutomationWorkflow).where(
                AutomationWorkflow.id == workflow_id,
                AutomationWorkflow.institution_id == institution_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_workflows(
        self,
        institution_id: str,
        *,
        location_id: str | None = None,
        status: AutomationWorkflowStatus | None = None,
    ) -> list[AutomationWorkflow]:
        stmt = (
            select(AutomationWorkflow)
            .where(AutomationWorkflow.institution_id == institution_id)
            .order_by(AutomationWorkflow.created_at.desc())
        )
        if location_id is not None:
            stmt = stmt.where(AutomationWorkflow.location_id == location_id)
        if status is not None:
            stmt = stmt.where(AutomationWorkflow.status == status.value)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_draft(
        self, workflow: AutomationWorkflow, **updates: Any
    ) -> AutomationWorkflow:
        if workflow.status not in _EDITABLE_STATUSES:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Workflow '{workflow.id}' is not in draft and cannot be edited.",
            )
        allowed = {"name", "description", "category", "location_id"}
        for key, value in updates.items():
            if key in allowed and value is not None:
                setattr(workflow, key, value)
        await self.session.flush()
        return workflow

    async def publish_version(
        self,
        workflow: AutomationWorkflow,
        definition: dict,
        *,
        content_classification: str | None = None,
        published_by_user_id: str | None = None,
    ) -> AutomationWorkflowVersion:
        """Snapshot the definition as an immutable version and activate the workflow."""
        if workflow.status not in _PUBLISHABLE_STATUSES:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Workflow status '{workflow.status}' cannot be published.",
            )

        try:
            WorkflowDefinition.model_validate(definition)
        except ValidationError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid workflow definition: {exc.error_count()} error(s). "
                       + "; ".join(e["msg"] for e in exc.errors()),
            ) from exc

        result = await self.session.execute(
            select(AutomationWorkflowVersion)
            .where(AutomationWorkflowVersion.workflow_id == workflow.id)
            .order_by(AutomationWorkflowVersion.version_number.desc())
            .limit(1)
        )
        last = result.scalar_one_or_none()
        next_number = (last.version_number + 1) if last else 1

        checksum = hashlib.sha256(
            json.dumps(definition, sort_keys=True).encode()
        ).hexdigest()

        version = AutomationWorkflowVersion(
            institution_id=workflow.institution_id,
            location_id=workflow.location_id,
            workflow_id=workflow.id,
            version_number=next_number,
            definition=definition,
            definition_checksum=checksum,
            content_classification=content_classification,
            published_by_user_id=published_by_user_id,
        )
        self.session.add(version)
        await self.session.flush()

        workflow.current_version_id = version.id
        workflow.status = AutomationWorkflowStatus.ACTIVE.value
        await self.session.flush()
        return version

    async def pause_workflow(self, workflow: AutomationWorkflow) -> AutomationWorkflow:
        if workflow.status != AutomationWorkflowStatus.ACTIVE.value:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="Only active workflows can be paused.",
            )
        workflow.status = AutomationWorkflowStatus.PAUSED.value
        await self.session.flush()
        return workflow

    async def resume_workflow(self, workflow: AutomationWorkflow) -> AutomationWorkflow:
        if workflow.status != AutomationWorkflowStatus.PAUSED.value:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="Only paused workflows can be resumed.",
            )
        workflow.status = AutomationWorkflowStatus.ACTIVE.value
        await self.session.flush()
        return workflow

    async def archive_workflow(self, workflow: AutomationWorkflow) -> AutomationWorkflow:
        if workflow.status == AutomationWorkflowStatus.ARCHIVED.value:
            return workflow
        workflow.status = AutomationWorkflowStatus.ARCHIVED.value
        await self.session.flush()
        return workflow
