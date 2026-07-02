"""FastAPI routes for automation workflow management and enrollment."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.app.api.deps import (
    get_current_institution_or_location_admin,
    get_current_institution_user,
)
from src.app.database import get_db_session
from sqlalchemy import select as sa_select
from src.app.models.automation_workflow import AutomationWorkflowRun, AutomationWorkflowVersion
from src.app.models.user import User
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.definition_service import AutomationWorkflowDefinitionService
from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
from src.app.services.automation.runtime_service import AutomationWorkflowRuntimeService
from src.app.services.automation.scheduler_service import AutomationWorkflowSchedulerService
from src.app.services.automation.step_dispatcher import WorkflowStepDispatcher

router = APIRouter(prefix="/automation/workflows", tags=["Automation Workflows"])

# Workflow configuration (create/update/lifecycle) is institution-scoped;
# location admins should not reconfigure institution-level workflows.
_InstitutionAdmin = Annotated[User, Depends(get_current_institution_user)]

# Enrollment and run reads are also scoped to the institution via RLS but
# location admins may trigger enrollments for patients at their clinic.
_InstitutionOrLocationAdmin = Annotated[User, Depends(get_current_institution_or_location_admin)]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    definition: dict[str, Any]


class WorkflowUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    definition: dict[str, Any] | None = None


class EnrollRequest(BaseModel):
    contact_id: str | None = None
    location_id: str | None = None
    trigger_ref_type: str | None = None
    trigger_ref_id: str | None = None
    idempotency_key: str
    trigger_metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowResponse(BaseModel):
    id: str
    name: str
    status: str
    trigger_type: str | None
    definition: dict[str, Any] | None
    current_version_id: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, wf: Any) -> "WorkflowResponse":
        return cls(
            id=str(wf.id),
            name=wf.name,
            status=wf.status,
            trigger_type=wf.trigger_type,
            definition=wf.definition,
            current_version_id=str(wf.current_version_id) if wf.current_version_id else None,
            created_at=wf.created_at,
            updated_at=wf.updated_at,
        )


class WorkflowRunResponse(BaseModel):
    id: str
    workflow_id: str
    status: str
    current_step_id: str | None
    outcome: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, run: Any) -> "WorkflowRunResponse":
        return cls(
            id=str(run.id),
            workflow_id=str(run.workflow_id),
            status=run.status,
            current_step_id=run.current_step_id,
            outcome=run.outcome,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _institution_id(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No institution context"
        )
    return str(user.institution_id)


async def _get_workflow_or_404(
    svc: AutomationWorkflowDefinitionService, workflow_id: str, institution_id: str
) -> Any:
    wf = await svc.get_workflow(workflow_id, institution_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return wf


# ---------------------------------------------------------------------------
# Workflow CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    data: WorkflowCreateRequest,
    current_user: _InstitutionAdmin,
) -> WorkflowResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await svc.create_draft(institution_id=inst_id, name=data.name)
        await svc.publish_version(wf, data.definition)
    return WorkflowResponse.from_model(wf)


@router.get("", response_model=list[WorkflowResponse])
async def list_workflows(current_user: _InstitutionAdmin) -> list[WorkflowResponse]:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        workflows = await svc.list_workflows(institution_id=inst_id)
    return [WorkflowResponse.from_model(wf) for wf in workflows]


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    current_user: _InstitutionOrLocationAdmin,
) -> WorkflowResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await _get_workflow_or_404(svc, workflow_id, inst_id)
    return WorkflowResponse.from_model(wf)


@router.patch("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: str,
    data: WorkflowUpdateRequest,
    current_user: _InstitutionAdmin,
) -> WorkflowResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await _get_workflow_or_404(svc, workflow_id, inst_id)
        if data.name is not None:
            wf.name = data.name
            await session.flush()
        if data.definition is not None:
            await svc.publish_version(wf, data.definition)
    return WorkflowResponse.from_model(wf)


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/publish", response_model=WorkflowResponse)
async def publish_workflow(
    workflow_id: str,
    current_user: _InstitutionAdmin,
) -> WorkflowResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await _get_workflow_or_404(svc, workflow_id, inst_id)
        await svc.publish_version(wf)
    return WorkflowResponse.from_model(wf)


@router.post("/{workflow_id}/pause", response_model=WorkflowResponse)
async def pause_workflow(
    workflow_id: str,
    current_user: _InstitutionAdmin,
) -> WorkflowResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await _get_workflow_or_404(svc, workflow_id, inst_id)
        await svc.pause_workflow(wf)
    return WorkflowResponse.from_model(wf)


@router.post("/{workflow_id}/resume", response_model=WorkflowResponse)
async def resume_workflow(
    workflow_id: str,
    current_user: _InstitutionAdmin,
) -> WorkflowResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await _get_workflow_or_404(svc, workflow_id, inst_id)
        await svc.resume_workflow(wf)
    return WorkflowResponse.from_model(wf)


@router.post("/{workflow_id}/archive", response_model=WorkflowResponse)
async def archive_workflow(
    workflow_id: str,
    current_user: _InstitutionAdmin,
) -> WorkflowResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await _get_workflow_or_404(svc, workflow_id, inst_id)
        await svc.archive_workflow(wf)
    return WorkflowResponse.from_model(wf)


# ---------------------------------------------------------------------------
# Enrollment and run management
# ---------------------------------------------------------------------------


@router.post(
    "/{workflow_id}/enroll",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def enroll_in_workflow(
    workflow_id: str,
    data: EnrollRequest,
    current_user: _InstitutionOrLocationAdmin,
) -> WorkflowRunResponse:
    inst_id = _institution_id(current_user)
    location_id = data.location_id or (
        str(current_user.location_id) if getattr(current_user, "location_id", None) else None
    )

    async with get_db_session() as session:
        def_svc = AutomationWorkflowDefinitionService(session)
        wf = await _get_workflow_or_404(def_svc, workflow_id, inst_id)

        if wf.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Workflow is not active (status={wf.status})",
            )
        if not wf.current_version_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workflow has no published version",
            )

        enroll_svc = AutomationWorkflowEnrollmentService(session)
        run, created = await enroll_svc.enroll(
            institution_id=inst_id,
            workflow_id=workflow_id,
            workflow_version_id=str(wf.current_version_id),
            contact_id=data.contact_id,
            location_id=location_id,
            trigger_type=wf.trigger_type,
            trigger_ref_type=data.trigger_ref_type,
            trigger_ref_id=data.trigger_ref_id,
            trigger_metadata=data.trigger_metadata,
            idempotency_key=data.idempotency_key,
        )

        if created:
            # Start run and advance inline. The first advance typically ends
            # at a WaitNode (one DB write for the timer). Move to a Celery
            # task if response latency becomes a concern at higher volume.
            version = await session.get(AutomationWorkflowVersion, str(wf.current_version_id))
            definition = WorkflowDefinition.model_validate(version.definition)
            runtime = AutomationWorkflowRuntimeService(session)
            scheduler = AutomationWorkflowSchedulerService(session)
            dispatcher = WorkflowStepDispatcher(session, runtime, scheduler)
            await runtime.start_run(run)
            await dispatcher.advance(
                run,
                definition,
                context=run.trigger_metadata or {},
                location_timezone="UTC",
            )

    return WorkflowRunResponse.from_model(run)


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunResponse])
async def list_runs(
    workflow_id: str,
    current_user: _InstitutionOrLocationAdmin,
    limit: int = Query(50, ge=1, le=500),
) -> list[WorkflowRunResponse]:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        result = await session.execute(
            sa_select(AutomationWorkflowRun)
            .where(
                AutomationWorkflowRun.workflow_id == workflow_id,
                AutomationWorkflowRun.institution_id == inst_id,
            )
            .order_by(AutomationWorkflowRun.created_at.desc())
            .limit(limit)
        )
        runs = result.scalars().all()
    return [WorkflowRunResponse.from_model(r) for r in runs]


@router.get("/{workflow_id}/runs/{run_id}", response_model=WorkflowRunResponse)
async def get_run_status(
    workflow_id: str,
    run_id: str,
    current_user: _InstitutionOrLocationAdmin,
) -> WorkflowRunResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if (
            run is None
            or str(run.institution_id) != inst_id
            or str(run.workflow_id) != workflow_id
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return WorkflowRunResponse.from_model(run)


@router.post("/{workflow_id}/runs/{run_id}/cancel", response_model=WorkflowRunResponse)
async def cancel_run(
    workflow_id: str,
    run_id: str,
    current_user: _InstitutionOrLocationAdmin,
) -> WorkflowRunResponse:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if (
            run is None
            or str(run.institution_id) != inst_id
            or str(run.workflow_id) != workflow_id
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        enroll_svc = AutomationWorkflowEnrollmentService(session)
        await enroll_svc.cancel_run(run)
    return WorkflowRunResponse.from_model(run)


# ---------------------------------------------------------------------------
# Bulk enrollment — Slice 12 (Plan 09)
# ---------------------------------------------------------------------------


class BulkEnrollItem(BaseModel):
    contact_id: str | None = None
    location_id: str | None = None
    trigger_ref_type: str | None = None
    trigger_ref_id: str | None = None
    idempotency_key: str
    trigger_metadata: dict[str, Any] = Field(default_factory=dict)


class BulkEnrollRequest(BaseModel):
    items: list[BulkEnrollItem] = Field(..., min_length=1, max_length=500)


class BulkEnrollResponse(BaseModel):
    enqueued: int
    workflow_id: str
    workflow_version_id: str


@router.post(
    "/{workflow_id}/bulk-enroll",
    response_model=BulkEnrollResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def bulk_enroll(
    workflow_id: str,
    data: BulkEnrollRequest,
    current_user: _InstitutionAdmin,
) -> BulkEnrollResponse:
    """Enqueue workflow enrollment for a list of contacts (up to 500 per request).

    Each item is dispatched as an independent Celery task with its own
    idempotency key. Returns 202 immediately — enrollment happens asynchronously.
    """
    from src.app.tasks.automation_workflow import enroll_and_start_workflow_run

    inst_id = _institution_id(current_user)

    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await _get_workflow_or_404(svc, workflow_id, inst_id)

        if wf.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Workflow is not active (status={wf.status})",
            )
        if not wf.current_version_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workflow has no published version",
            )

        version_id = str(wf.current_version_id)
        trigger_type = wf.trigger_type

    for item in data.items:
        enroll_and_start_workflow_run.apply_async(
            kwargs={
                "institution_id": inst_id,
                "workflow_id": workflow_id,
                "workflow_version_id": version_id,
                "contact_id": item.contact_id,
                "location_id": item.location_id,
                "trigger_type": trigger_type,
                "trigger_ref_type": item.trigger_ref_type,
                "trigger_ref_id": item.trigger_ref_id,
                "idempotency_key": item.idempotency_key,
                "trigger_metadata": item.trigger_metadata,
            },
            queue="workflow",
        )

    return BulkEnrollResponse(
        enqueued=len(data.items),
        workflow_id=workflow_id,
        workflow_version_id=version_id,
    )
