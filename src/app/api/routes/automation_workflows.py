"""FastAPI routes for automation workflow management and enrollment."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError

from src.app.api.deps import (
    get_current_institution_or_location_admin,
    get_current_institution_user,
)
from src.app.database import get_db_session
from sqlalchemy import select as sa_select
from src.app.models.automation_workflow import (
    AutomationWorkflowRun,
    AutomationWorkflowStatus,
    AutomationWorkflowVersion,
)
from src.app.models.outbound_halt import OutboundEmergencyHalt
from src.app.models.user import User
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.definition_service import AutomationWorkflowDefinitionService
from src.app.services.automation.channel_readiness import ChannelReadinessService
from src.app.services.automation.content_compliance_validator import ContentComplianceValidator
from src.app.services.automation.dry_run import simulate_run
from src.app.services.automation.merge_field_catalog import fields_for
from src.app.services.automation.validation_service import WorkflowValidationService
from src.app.services.automation.enrollment_service import AutomationWorkflowEnrollmentService
from src.app.services.automation.step_dispatcher import build_dispatcher

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
    location_id: str | None
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
            location_id=str(wf.location_id) if wf.location_id else None,
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


class WorkflowVersionResponse(BaseModel):
    id: str
    workflow_id: str
    version_number: int
    definition: dict[str, Any]
    definition_checksum: str | None
    content_classification: str | None
    published_by_user_id: str | None
    published_at: datetime
    created_at: datetime
    is_current: bool

    @classmethod
    def from_model(cls, v: Any, *, current_version_id: str | None) -> "WorkflowVersionResponse":
        return cls(
            id=str(v.id),
            workflow_id=str(v.workflow_id),
            version_number=v.version_number,
            definition=v.definition,
            definition_checksum=v.definition_checksum,
            content_classification=v.content_classification,
            published_by_user_id=(
                str(v.published_by_user_id) if v.published_by_user_id else None
            ),
            published_at=v.published_at,
            created_at=v.created_at,
            is_current=bool(current_version_id) and str(v.id) == str(current_version_id),
        )


class ValidateDefinitionRequest(BaseModel):
    definition: dict[str, Any]


class ValidationIssueResponse(BaseModel):
    severity: Literal["error", "warning"] = "error"
    node_id: str | None = None
    field_path: list[Any] = Field(default_factory=list)
    message: str
    code: str | None = None


class ValidateDefinitionResponse(BaseModel):
    valid: bool
    issues: list[ValidationIssueResponse] = Field(default_factory=list)


class MergeFieldResponse(BaseModel):
    name: str
    token: str
    label: str
    description: str
    sample: str
    group: str
    availability: str
    requires: list[str] = Field(default_factory=list)
    phi_level: str
    channels: list[str] = Field(default_factory=list)
    trigger_types: list[str] = Field(default_factory=list)


class ChannelReadinessDetail(BaseModel):
    channel: str
    ready: bool
    reason: str | None = None


class ChannelReadinessResponse(BaseModel):
    sms: bool
    email: bool
    voice_configurable: bool
    details: list[ChannelReadinessDetail] = Field(default_factory=list)


class DryRunRequest(BaseModel):
    definition: dict[str, Any]
    context: dict[str, Any] | None = None
    condition_choices: dict[str, bool] | None = None


class DryRunStepResponse(BaseModel):
    node_id: str
    node_type: str
    summary: str
    detail: str | None = None


class DryRunResultResponse(BaseModel):
    steps: list[DryRunStepResponse] = Field(default_factory=list)
    outcome: str | None = None
    truncated: bool = False


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
    wf = await svc.get_workflow(institution_id, workflow_id)
    if wf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return wf


def _node_id_for_loc(loc: tuple, definition: dict[str, Any]) -> str | None:
    """Resolve the declared node id for a pydantic error location.

    Pydantic reports node-level errors with a location like
    ``("nodes", <index>, ...)``; translate the positional index back to the
    node's own ``id`` so the builder can highlight the offending node.
    """
    if len(loc) >= 2 and loc[0] == "nodes" and isinstance(loc[1], int):
        nodes = definition.get("nodes")
        if isinstance(nodes, list) and 0 <= loc[1] < len(nodes):
            node = nodes[loc[1]]
            if isinstance(node, dict) and node.get("id") is not None:
                return str(node["id"])
    return None


def _issue_from_pydantic_error(
    err: dict[str, Any], definition: dict[str, Any]
) -> ValidationIssueResponse:
    loc = tuple(err.get("loc", ()))
    message = str(err.get("msg", "invalid"))
    # Graph-structure errors raised in the model validator are prefixed by
    # pydantic with "Value error, " — strip it for a cleaner message.
    if message.startswith("Value error, "):
        message = message[len("Value error, "):]
    return ValidationIssueResponse(
        node_id=_node_id_for_loc(loc, definition),
        field_path=list(loc),
        message=message,
    )


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


@router.post("/validate", response_model=ValidateDefinitionResponse)
async def validate_definition(
    data: ValidateDefinitionRequest,
    current_user: _InstitutionAdmin,
) -> ValidateDefinitionResponse:
    """Validate a workflow definition against the authoritative backend schema
    without persisting anything.

    Mirrors exactly what publish enforces (which otherwise surfaces as a 422):
    structural + reachability + consent/content-class + the Plan-12/readiness
    seams, returned up-front and node-linked (with warnings) so the builder can
    block/annotate before the user commits to publishing.
    """
    inst_id = _institution_id(current_user)
    # Pure validation — no persistence, so no location context is supplied. The
    # Plan-10 readiness checker short-circuits on a null location (readiness is a
    # per-location property surfaced by GET /channel-readiness), so it adds no
    # issues here. The Plan-12 content validator (promotional-in-exempt / PHI) is
    # pure text analysis of the definition and needs no session, so it runs here
    # too — the builder sees the same content issues publish will enforce.
    issues = await WorkflowValidationService(
        session=None,
        content_validator=ContentComplianceValidator(),
        readiness_checker=ChannelReadinessService(None),
    ).validate(data.definition, institution_id=inst_id)
    responses = [
        ValidationIssueResponse(
            severity=i.severity,
            node_id=i.node_id,
            field_path=list(i.field_path),
            message=i.message,
            code=i.code,
        )
        for i in issues
    ]
    valid = not any(i.severity == "error" for i in issues)
    return ValidateDefinitionResponse(valid=valid, issues=responses)


@router.post("/dry-run", response_model=DryRunResultResponse)
async def dry_run_definition(
    data: DryRunRequest,
    current_user: _InstitutionAdmin,
) -> DryRunResultResponse:
    """Simulate a run against the authoritative backend definition + merge renderer
    without persisting or sending. Powers the builder's test-run preview so it can't
    drift from real engine semantics. Structurally-invalid definitions return 422."""
    _institution_id(current_user)  # authz / institution context
    try:
        definition = WorkflowDefinition.model_validate(data.definition)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid workflow definition: {exc.error_count()} error(s)",
        ) from exc

    result = simulate_run(
        definition, context=data.context, condition_choices=data.condition_choices
    )
    return DryRunResultResponse(
        steps=[
            DryRunStepResponse(
                node_id=s.node_id, node_type=s.node_type, summary=s.summary, detail=s.detail
            )
            for s in result.steps
        ],
        outcome=result.outcome,
        truncated=result.truncated,
    )


@router.get("", response_model=list[WorkflowResponse])
async def list_workflows(current_user: _InstitutionAdmin) -> list[WorkflowResponse]:
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        workflows = await svc.list_workflows(institution_id=inst_id)
        return [WorkflowResponse.from_model(wf) for wf in workflows]


@router.get("/merge-fields", response_model=list[MergeFieldResponse])
async def list_merge_fields(
    current_user: _InstitutionOrLocationAdmin,
    trigger_type: Annotated[str | None, Query()] = None,
    channel: Annotated[str | None, Query()] = None,
    include_unavailable: Annotated[bool, Query()] = False,
) -> list[MergeFieldResponse]:
    """Return the merge-field catalog the message renderer substitutes.

    Sourced from the backend catalog so the builder's insert-field menu can
    filter by trigger/channel without drifting from render semantics.

    NOTE: declared before ``/{workflow_id}`` so this literal path is not
    captured as a workflow id by the parameterised route.
    """
    return [
        MergeFieldResponse(
            name=f.name,
            token=f.token,
            label=f.label,
            description=f.description,
            sample=f.sample,
            group=f.group,
            availability=f.availability,
            requires=list(f.requires),
            phi_level=f.phi_level,
            channels=list(f.channels),
            trigger_types=list(f.triggers),
        )
        for f in fields_for(
            trigger_type=trigger_type,
            channel=channel,
            include_unavailable=include_unavailable,
        )
    ]


@router.get("/channel-readiness", response_model=ChannelReadinessResponse)
async def get_channel_readiness(
    current_user: _InstitutionAdmin,
    location_id: str = Query(..., description="Location to check channel readiness for"),
) -> ChannelReadinessResponse:
    """Report whether SMS / email / voice are provisioned for a location so the
    builder can surface missing setup before publish (B6).

    Readiness is computed from existing credentials (Twilio sender number /
    sub-account creds, email from-address, per-location Retell agent) — there is
    no readiness state table. Provisioning stays manual in this MVP, so these are
    advisory: an unready channel warns at publish but does not block it.

    NOTE: declared before ``/{workflow_id}`` so this literal path is not captured
    as a workflow id by the parameterised route.
    """
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        report = await ChannelReadinessService(session).readiness_for_location(
            institution_id=inst_id, location_id=location_id
        )
    return ChannelReadinessResponse(
        sms=report.sms,
        email=report.email,
        voice_configurable=report.voice_configurable,
        details=[ChannelReadinessDetail(**d) for d in report.details],
    )


# ---------------------------------------------------------------------------
# Outbound emergency halt — institution-level kill switch (Plan 12)
# ---------------------------------------------------------------------------


class OutboundHaltResponse(BaseModel):
    halted: bool
    halt_id: str | None = None
    reason: str | None = None
    halted_at: datetime | None = None
    halted_by_user_id: str | None = None
    # Number of in-flight runs terminated when the halt was activated.
    halted_runs: int | None = None


@router.get("/outbound-halt", response_model=OutboundHaltResponse)
async def get_outbound_halt_status(
    current_user: _InstitutionAdmin,
) -> OutboundHaltResponse:
    """Return the current outbound halt status for this institution.

    NOTE: declared before ``/{workflow_id}`` so this literal path is not
    captured as a workflow id by the parameterised route.
    """
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        result = await session.execute(
            sa_select(OutboundEmergencyHalt)
            .where(
                OutboundEmergencyHalt.institution_id == inst_id,
                OutboundEmergencyHalt.released_at.is_(None),
            )
            .limit(1)
        )
        halt = result.scalar_one_or_none()
    if halt is None:
        return OutboundHaltResponse(halted=False)
    return OutboundHaltResponse(
        halted=True,
        halt_id=halt.id,
        reason=halt.reason,
        halted_at=halt.created_at,
        halted_by_user_id=halt.halted_by_user_id,
    )


class OutboundHaltRequest(BaseModel):
    reason: str | None = None


@router.post(
    "/outbound-halt",
    response_model=OutboundHaltResponse,
    status_code=status.HTTP_201_CREATED,
)
async def activate_outbound_halt(
    data: OutboundHaltRequest,
    current_user: _InstitutionAdmin,
) -> OutboundHaltResponse:
    """Activate institution-wide outbound campaign halt. Idempotent — if already
    halted, returns the existing active halt without creating a duplicate."""
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        existing = (
            await session.execute(
                sa_select(OutboundEmergencyHalt)
                .where(
                    OutboundEmergencyHalt.institution_id == inst_id,
                    OutboundEmergencyHalt.released_at.is_(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

        if existing:
            return OutboundHaltResponse(
                halted=True,
                halt_id=existing.id,
                reason=existing.reason,
                halted_at=existing.created_at,
                halted_by_user_id=existing.halted_by_user_id,
            )

        halt = OutboundEmergencyHalt(
            institution_id=inst_id,
            halted_by_user_id=str(current_user.id),
            reason=data.reason,
        )
        session.add(halt)
        await session.flush()
        halt_id = halt.id
        halt_reason = halt.reason
        halt_created = halt.created_at

        # A halt is a kill switch: terminate in-flight runs now (cancel their
        # timers) so waiting runs can't fire during the halt — not just block the
        # next send. New sends are also blocked by the compliance gate reading
        # this halt row.
        def_svc = AutomationWorkflowDefinitionService(session)
        halted_runs = await def_svc.emergency_halt_institution(
            institution_id=inst_id,
            actor_user_id=str(current_user.id),
            reason=data.reason or "emergency_halt",
        )
        await session.commit()

    return OutboundHaltResponse(
        halted=True,
        halt_id=halt_id,
        reason=halt_reason,
        halted_at=halt_created,
        halted_by_user_id=str(current_user.id),
        halted_runs=halted_runs,
    )


@router.delete("/outbound-halt", response_model=OutboundHaltResponse)
async def release_outbound_halt(
    current_user: _InstitutionAdmin,
) -> OutboundHaltResponse:
    """Release the active outbound halt. Returns 404 if no halt is active."""
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        halt = (
            await session.execute(
                sa_select(OutboundEmergencyHalt)
                .where(
                    OutboundEmergencyHalt.institution_id == inst_id,
                    OutboundEmergencyHalt.released_at.is_(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

        if halt is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active outbound halt for this institution",
            )

        halt.released_at = datetime.now(tz=timezone.utc)
        halt.released_by_user_id = str(current_user.id)
        await session.commit()

    return OutboundHaltResponse(halted=False)


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


@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionResponse])
async def list_workflow_versions(
    workflow_id: str,
    current_user: _InstitutionOrLocationAdmin,
) -> list[WorkflowVersionResponse]:
    """List every published version of a workflow, newest first.

    The definition schema is ``extra="forbid"`` so versions are immutable
    snapshots; this exposes the full history the model already records (only
    ``current_version_id`` was previously reachable via the API).
    """
    inst_id = _institution_id(current_user)
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await _get_workflow_or_404(svc, workflow_id, inst_id)
        versions = sorted(
            wf.versions, key=lambda v: v.version_number, reverse=True
        )
        return [
            WorkflowVersionResponse.from_model(v, current_version_id=wf.current_version_id)
            for v in versions
        ]


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
            await session.refresh(wf, attribute_names=["updated_at"])
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
            # Single wiring path: injects the real ComplianceGateService and
            # resolves the location timezone (never NoOp / never hardcoded UTC).
            # The live PMS revalidator guards appointment-triggered sends against
            # cancelled/rescheduled appointments; it is a no-op for other runs.
            from src.app.services.automation.revalidation import PmsLiveRevalidationService

            dispatcher, location_timezone = await build_dispatcher(
                session,
                location_id=location_id,
                revalidator=PmsLiveRevalidationService(session),
            )
            await dispatcher.runtime.start_run(run)
            await dispatcher.advance(
                run,
                definition,
                context=run.trigger_metadata or {},
                location_timezone=location_timezone,
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


class WorkflowHaltResponse(BaseModel):
    workflow_id: str
    halted_runs: int
    status: str


@router.post("/{workflow_id}/emergency-halt", response_model=WorkflowHaltResponse)
async def emergency_halt_workflow(
    workflow_id: str,
    current_user: _InstitutionAdmin,
    data: OutboundHaltRequest | None = None,
) -> WorkflowHaltResponse:
    """Emergency-halt a single workflow: terminate all in-flight runs on its
    current version (cancelling their timers) and pause the workflow so no new
    enrollments start. Distinct from pause, which leaves in-flight runs to finish."""
    inst_id = _institution_id(current_user)
    reason = (data.reason if data else None) or "emergency_halt"
    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await _get_workflow_or_404(svc, workflow_id, inst_id)
        halted = 0
        if wf.current_version_id:
            halted = await svc.emergency_halt_version(
                institution_id=inst_id,
                workflow_version_id=str(wf.current_version_id),
                actor_user_id=str(current_user.id),
                reason=reason,
            )
        if wf.status == AutomationWorkflowStatus.ACTIVE.value:
            await svc.pause_workflow(wf)
        status_val = wf.status
        await session.commit()
    return WorkflowHaltResponse(
        workflow_id=workflow_id, halted_runs=halted, status=status_val
    )
