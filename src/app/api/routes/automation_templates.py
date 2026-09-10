"""FastAPI routes for campaign template browsing and instantiation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.app.api.deps import (
    get_current_institution_or_location_admin,
)
from src.app.api.routes.automation_workflows import (
    WorkflowResponse,
    get_current_campaign_manager,
)
from src.app.database import get_db_session
from src.app.models.institution import Institution
from src.app.models.institution_appointment_type import InstitutionAppointmentType
from src.app.models.institution_descriptor import InstitutionDescriptor
from src.app.models.institution_location import InstitutionLocation
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.user import User, UserRole
from src.app.services.audit_decorator import audit
from src.app.services.automation.campaign_templates import (
    CampaignTemplate,
    get_template,
    instantiate_definition,
    list_templates,
    template_pms_types,
)
from src.app.services.automation.definition_service import (
    AutomationWorkflowDefinitionService,
)
from src.app.services.automation.pms_capability_service import (
    PmsCapabilityEvaluation,
    PmsCapabilityService,
)

router = APIRouter(prefix="/automation/templates", tags=["Automation Templates"])

_InstitutionOrLocationAdmin = Annotated[
    User, Depends(get_current_institution_or_location_admin)
]
_CampaignManager = Annotated[User, Depends(get_current_campaign_manager)]


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CampaignTemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    trigger_type: str
    definition: dict[str, Any]
    tags: list[str]
    category: str
    metadata: dict[str, Any]
    pms_types: list[str] = Field(default_factory=list)

    @classmethod
    def from_template(
        cls,
        t: CampaignTemplate,
        *,
        pms_capability_evaluation: PmsCapabilityEvaluation | None = None,
    ) -> "CampaignTemplateResponse":
        metadata = asdict(t.metadata)
        if pms_capability_evaluation is not None:
            metadata["pms_capability_evaluation"] = pms_capability_evaluation.as_dict()
        return cls(
            id=t.id,
            name=t.name,
            description=t.description,
            trigger_type=t.trigger_type,
            definition=t.definition,
            tags=t.tags,
            category=t.category,
            metadata=metadata,
            pms_types=sorted(template_pms_types(t)),
        )


class CampaignTemplateInstantiateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    location_id: str | None = None
    voice_profile_id: str | None = Field(None, max_length=255)
    voice_agent_id: str | None = Field(None, max_length=255)
    setup_options: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[CampaignTemplateResponse])
async def list_campaign_templates(
    current_user: _InstitutionOrLocationAdmin,
    location_id: Annotated[str | None, Query()] = None,
) -> list[CampaignTemplateResponse]:
    """List the campaign templates available to this institution's PMS."""
    location_id = _location_id_for_user(current_user, location_id)
    templates = list_templates()
    if not location_id:
        pms_type = await _institution_pms_type(current_user)
        return [
            CampaignTemplateResponse.from_template(t)
            for t in templates
            if pms_type in template_pms_types(t)
        ]

    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(
            current_user,
            session,
            location_id,
        )
        templates = [
            t for t in templates if institution.pms_type in template_pms_types(t)
        ]
        evaluator = PmsCapabilityService(session)
        responses: list[CampaignTemplateResponse] = []
        for template in templates:
            requirements = template.metadata.pms_capability_requirements
            evaluation = (
                await evaluator.evaluate_location(
                    institution=institution,
                    location=location,
                    requirements=requirements,
                )
                if requirements
                else None
            )
            responses.append(
                CampaignTemplateResponse.from_template(
                    template,
                    pms_capability_evaluation=evaluation,
                )
            )
        return responses


@router.get("/{template_id}", response_model=CampaignTemplateResponse)
async def get_campaign_template(
    template_id: str,
    current_user: _InstitutionOrLocationAdmin,
    location_id: Annotated[str | None, Query()] = None,
) -> CampaignTemplateResponse:
    """Get a single campaign template by ID."""
    location_id = _location_id_for_user(current_user, location_id)
    template = get_template(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
        )
    if not location_id:
        pms_type = await _institution_pms_type(current_user)
        if pms_type not in template_pms_types(template):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
            )
        return CampaignTemplateResponse.from_template(template)

    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(
            current_user,
            session,
            location_id,
        )
        if institution.pms_type not in template_pms_types(template):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
            )
        evaluation = None
        if template.metadata.pms_capability_requirements:
            evaluation = await PmsCapabilityService(session).evaluate_location(
                institution=institution,
                location=location,
                requirements=template.metadata.pms_capability_requirements,
            )
        return CampaignTemplateResponse.from_template(
            template,
            pms_capability_evaluation=evaluation,
        )


@router.post(
    "/{template_id}/instantiate",
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
)
@audit(
    AuditAction.CAMPAIGN_CREATE,
    resource=lambda *args, **kwargs: (
        f"campaign:from-template:{kwargs.get('template_id')}"
    ),
    actor=AuditActor.ADMIN,
)
async def instantiate_template(
    template_id: str,
    current_user: _CampaignManager,
    data: CampaignTemplateInstantiateRequest | None = None,
) -> WorkflowResponse:
    """Instantiate a campaign template as a new workflow.

    The template's definition is validated and published as version 1, then the
    resulting workflow is paused. The engine has no draft-with-definition
    lifecycle (a definition only ever lives inside a published version), so
    pausing after publish lets admins review required setup before any event can
    enroll contacts.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No institution context"
        )

    template = get_template(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
        )

    pms_type = await _institution_pms_type(current_user)
    if pms_type not in template_pms_types(template):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "template_unsupported_for_pms",
                "message": (
                    "This template is not available for this practice's "
                    "management software."
                ),
            },
        )

    data = data or CampaignTemplateInstantiateRequest()
    location_id = _location_id_for_user(current_user, data.location_id)
    try:
        definition = instantiate_definition(
            template,
            voice_profile_id=data.voice_profile_id,
            voice_agent_id=data.voice_agent_id,
            setup_options=data.setup_options,
            pms_type=pms_type,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    async with get_db_session() as session:
        setup_options = dict(data.setup_options)
        classification_field = _classification_setup_field(template)
        if classification_field and classification_field in setup_options:
            if not location_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="location_id is required to select appointment classifications",
                )
            setup_options[classification_field] = await _validated_classifications(
                session,
                institution_id=str(current_user.institution_id),
                location_id=location_id,
                pms_type=pms_type,
                value=setup_options[classification_field],
            )

        if classification_field and classification_field in setup_options:
            definition = instantiate_definition(
                template,
                voice_profile_id=data.voice_profile_id,
                voice_agent_id=data.voice_agent_id,
                setup_options=setup_options,
                pms_type=pms_type,
            )

        resolved_location: tuple[Institution, InstitutionLocation] | None = None
        if location_id and (
            current_user.role == UserRole.LOCATION_ADMIN.value
            or bool(template.metadata.pms_capability_requirements)
        ):
            resolved_location = await _resolve_institution_location(
                current_user,
                session,
                location_id,
            )
        if template.metadata.pms_capability_requirements:
            if not location_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="location_id is required to verify PMS capability for this template",
                )
            assert resolved_location is not None
            institution, location = resolved_location
            evaluation = await PmsCapabilityService(session).evaluate_location(
                institution=institution,
                location=location,
                requirements=template.metadata.pms_capability_requirements,
            )
            if not evaluation.supported:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "unsupported_pms_capability",
                        "message": evaluation.message,
                        "pms_capability_evaluation": evaluation.as_dict(),
                    },
                )

        user_id = str(current_user.id) if getattr(current_user, "id", None) else None
        svc = AutomationWorkflowDefinitionService(session)
        wf = await svc.create_draft(
            institution_id=str(current_user.institution_id),
            name=(data.name.strip() if data.name else template.name),
            location_id=location_id,
            description=template.description,
            category=template.category,
            created_by_user_id=user_id,
        )
        await svc.publish_version(
            wf,
            definition,
            content_classification=template.metadata.default_compliance_content_class,
            published_by_user_id=user_id,
        )
        await svc.pause_workflow(wf)
        return WorkflowResponse.from_model(wf)


def _classification_setup_field(template: CampaignTemplate) -> str | None:
    ids = {
        str(field.get("id"))
        for field in template.metadata.setup_fields
        if isinstance(field, dict)
    }
    for field_id in ("appointment_classifications", "post_op_classifications"):
        if field_id in ids:
            return field_id
    return None


async def _validated_classifications(
    session,
    *,
    institution_id: str,
    location_id: str,
    pms_type: str,
    value: Any,
) -> list[dict[str, str]]:
    """Resolve submitted selector values to the current PMS cache."""
    if not isinstance(value, list) or not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one appointment classification is required",
        )

    submitted_id_list = [
        str(item.get("id") or item.get("source_id") or "").strip()
        for item in value
        if isinstance(item, dict)
    ]
    submitted_ids = set(submitted_id_list)
    if "" in submitted_ids or len(submitted_id_list) != len(value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Appointment classifications must be selected from the current location",
        )

    if pms_type == "nexhealth":
        result = await session.execute(
            select(InstitutionAppointmentType).where(
                InstitutionAppointmentType.institution_id == institution_id,
                InstitutionAppointmentType.location_id == location_id,
                InstitutionAppointmentType.source == "nexhealth",
                InstitutionAppointmentType.is_active.is_(True),
            )
        )
    elif pms_type == "gotracker":
        result = await session.execute(
            select(InstitutionDescriptor).where(
                InstitutionDescriptor.institution_id == institution_id,
                InstitutionDescriptor.location_id == location_id,
                InstitutionDescriptor.source == "gotracker",
                InstitutionDescriptor.descriptor_type == "GoTracker Reason",
                InstitutionDescriptor.is_active.is_(True),
            )
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Appointment classifications require an integrated practice management system",
        )

    rows = result.scalars().all()
    by_id = {str(row.source_id): row for row in rows}
    missing = submitted_ids - set(by_id)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="One or more appointment classifications are stale or invalid for this location",
        )
    return [
        {"id": source_id, "name": str(by_id[source_id].name)}
        for source_id in dict.fromkeys(submitted_id_list)
    ]


async def _institution_pms_type(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No institution context"
        )
    async with get_db_session() as session:
        institution = await session.get(Institution, str(user.institution_id))
    return institution.pms_type if institution else "none"


def _location_id_for_user(user: User, location_id: str | None) -> str | None:
    """Pin location admins to their assigned clinic for every template path."""
    if user.role != UserRole.LOCATION_ADMIN.value:
        return location_id
    if not user.location_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Location-scoped account is missing location assignment",
        )
    own_location_id = str(user.location_id)
    if location_id and str(location_id) != own_location_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot manage campaigns for another location",
        )
    return own_location_id


async def _resolve_institution_location(
    user: User,
    session,
    location_id: str,
) -> tuple[Institution, InstitutionLocation]:
    if not user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No institution context"
        )

    if user.role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
        if not user.location_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Location-scoped user missing location assignment",
            )
        if str(location_id) != str(user.location_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot access another location",
            )

    institution = (
        await session.execute(
            select(Institution).where(
                Institution.id == str(user.institution_id),
                Institution.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if institution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found"
        )

    location = (
        await session.execute(
            select(InstitutionLocation).where(
                InstitutionLocation.id == str(location_id),
                InstitutionLocation.institution_id == str(institution.id),
                InstitutionLocation.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active location found for institution",
        )

    return institution, location
