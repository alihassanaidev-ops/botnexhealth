"""Workflow definition lifecycle: create draft, publish version, pause, archive."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationWorkflow,
    AutomationWorkflowEvent,
    AutomationWorkflowRun,
    AutomationWorkflowStatus,
    AutomationWorkflowVersion,
)
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService
from src.app.services.automation.channel_readiness import ChannelReadinessService
from src.app.services.automation.content_compliance_validator import ContentComplianceValidator
from src.app.services.automation.validation_service import WorkflowValidationService

logger = logging.getLogger(__name__)

_EDITABLE_STATUSES = {AutomationWorkflowStatus.DRAFT.value}
_PUBLISHABLE_STATUSES = {
    AutomationWorkflowStatus.DRAFT.value,
    AutomationWorkflowStatus.PAUSED.value,
}
# Non-terminal runs that an emergency halt must actively terminate.
_IN_FLIGHT_RUN_STATUSES = (
    AutomationRunStatus.PENDING.value,
    AutomationRunStatus.RUNNING.value,
    AutomationRunStatus.WAITING.value,
)


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
            select(AutomationWorkflow)
            .options(selectinload(AutomationWorkflow.current_version))
            .where(
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
            .options(selectinload(AutomationWorkflow.current_version))
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
        definition: dict | None = None,
        *,
        content_classification: str | None = None,
        published_by_user_id: str | None = None,
    ) -> AutomationWorkflowVersion:
        """Snapshot the definition as an immutable version and activate the workflow.

        If *definition* is omitted the current version's definition is reused,
        which is the correct behaviour for re-activating a paused workflow.
        """
        if workflow.status not in _PUBLISHABLE_STATUSES:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Workflow status '{workflow.status}' cannot be published.",
            )

        if definition is None:
            if workflow.current_version is None:
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="No definition provided and workflow has no existing version.",
                )
            definition = workflow.current_version.definition

        # Authoritative validation: structural + consent/content-class + the
        # Plan-12/Plan-10 seams. Publish is fail-closed on any error-severity issue.
        issues = await WorkflowValidationService(
            self.session,
            content_validator=ContentComplianceValidator(),
            readiness_checker=ChannelReadinessService(self.session),
        ).validate(
            definition,
            institution_id=workflow.institution_id,
            location_id=workflow.location_id,
        )
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot publish: {len(errors)} error(s). "
                + "; ".join(e.message for e in errors),
            )

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
        workflow.current_version = version
        workflow.status = AutomationWorkflowStatus.ACTIVE.value
        await self.session.flush()
        await self.session.refresh(workflow, attribute_names=["updated_at"])
        return version

    async def pause_workflow(self, workflow: AutomationWorkflow) -> AutomationWorkflow:
        if workflow.status != AutomationWorkflowStatus.ACTIVE.value:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="Only active workflows can be paused.",
            )
        workflow.status = AutomationWorkflowStatus.PAUSED.value
        await self.session.flush()
        await self.session.refresh(workflow, attribute_names=["updated_at"])
        return workflow

    async def resume_workflow(self, workflow: AutomationWorkflow) -> AutomationWorkflow:
        if workflow.status != AutomationWorkflowStatus.PAUSED.value:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="Only paused workflows can be resumed.",
            )
        workflow.status = AutomationWorkflowStatus.ACTIVE.value
        await self.session.flush()
        await self.session.refresh(workflow, attribute_names=["updated_at"])
        return workflow

    async def archive_workflow(self, workflow: AutomationWorkflow) -> AutomationWorkflow:
        if workflow.status == AutomationWorkflowStatus.ARCHIVED.value:
            return workflow
        workflow.status = AutomationWorkflowStatus.ARCHIVED.value
        await self.session.flush()
        await self.session.refresh(workflow, attribute_names=["updated_at"])
        return workflow

    # ------------------------------------------------------------------
    # Emergency halt — terminate in-flight runs (distinct from pause)
    # ------------------------------------------------------------------
    #
    # Pause only stops *new* enrollment and defers waiting runs. Emergency halt
    # is the operator/compliance kill switch: it terminates every in-flight run
    # mid-flight and cancels its pending timers, so a version found non-compliant
    # (bad content, missing consent path) stops sending immediately.

    async def emergency_halt_version(
        self,
        *,
        institution_id: str,
        workflow_version_id: str,
        actor_user_id: str | None = None,
        reason: str = "emergency_halt",
    ) -> int:
        """Terminate all in-flight runs on a specific workflow version. Returns count."""
        stmt = select(AutomationWorkflowRun).where(
            AutomationWorkflowRun.institution_id == institution_id,
            AutomationWorkflowRun.workflow_version_id == workflow_version_id,
            AutomationWorkflowRun.status.in_(_IN_FLIGHT_RUN_STATUSES),
        )
        return await self._halt_runs(stmt, actor_user_id=actor_user_id, reason=reason)

    async def emergency_halt_institution(
        self,
        *,
        institution_id: str,
        actor_user_id: str | None = None,
        reason: str = "emergency_halt",
    ) -> int:
        """Terminate all in-flight runs for an institution (institution-wide halt)."""
        stmt = select(AutomationWorkflowRun).where(
            AutomationWorkflowRun.institution_id == institution_id,
            AutomationWorkflowRun.status.in_(_IN_FLIGHT_RUN_STATUSES),
        )
        return await self._halt_runs(stmt, actor_user_id=actor_user_id, reason=reason)

    async def _halt_runs(self, stmt, *, actor_user_id: str | None, reason: str) -> int:
        result = await self.session.execute(stmt)
        runs = list(result.scalars().all())
        if not runs:
            return 0
        enrollment = AutomationWorkflowEnrollmentService(self.session)
        scheduler = AutomationWorkflowSchedulerService(self.session)
        for run in runs:
            await scheduler.cancel_timers_for_run(run.id)
            await enrollment.cancel_run(run, reason=reason)
            self.session.add(
                AutomationWorkflowEvent(
                    institution_id=run.institution_id,
                    location_id=run.location_id,
                    workflow_run_id=run.id,
                    event_type="run.emergency_halted",
                    event_metadata={"reason": reason, "actor_user_id": actor_user_id},
                )
            )
        await self.session.flush()
        logger.info(
            "emergency_halt: terminated %d in-flight run(s) reason=%s actor=%s",
            len(runs), reason, actor_user_id,
        )
        return len(runs)
