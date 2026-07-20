"""FastAPI routes for campaign template browsing and instantiation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.app.api.deps import (
    get_current_institution_or_location_admin,
    get_current_institution_user,
)
from src.app.api.routes.automation_workflows import WorkflowResponse
from src.app.database import get_db_session
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.user import User, UserRole
from src.app.services.automation.campaign_templates import (
    CampaignTemplate,
    get_template,
    instantiate_definition,
    list_templates,
)
from src.app.services.automation.definition_service import AutomationWorkflowDefinitionService
from src.app.services.automation.pms_capability_service import (
    PmsCapabilityEvaluation,
    PmsCapabilityService,
)

router = APIRouter(prefix="/automation/templates", tags=["Automation Templates"])

_InstitutionAdmin = Annotated[User, Depends(get_current_institution_user)]
_InstitutionOrLocationAdmin = Annotated[User, Depends(get_current_institution_or_location_admin)]


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
        )


class CampaignTemplateInstantiateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    location_id: str | None = None
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
    """List all available campaign templates."""
    templates = list_templates()
    if not location_id:
        return [CampaignTemplateResponse.from_template(t) for t in templates]

    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(
            current_user,
            session,
            location_id,
        )
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
    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    if not location_id:
        return CampaignTemplateResponse.from_template(template)

    async with get_db_session() as session:
        institution, location = await _resolve_institution_location(
            current_user,
            session,
            location_id,
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
async def instantiate_template(
    template_id: str,
    current_user: _InstitutionAdmin,
    data: CampaignTemplateInstantiateRequest | None = None,
) -> WorkflowResponse:
    """Instantiate a campaign template as a new workflow.

    The template's definition is validated and published as version 1, so the
    resulting workflow is immediately ``active`` — consistent with
    ``POST /automation/workflows``. The engine has no draft-with-definition
    lifecycle (a definition only ever lives inside a published version), so a
    true "draft from template" is a documented backend follow-up; callers who
    want to review before it runs can immediately pause it.
    """
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No institution context")

    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    data = data or CampaignTemplateInstantiateRequest()
    try:
        definition = instantiate_definition(template, voice_agent_id=data.voice_agent_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    async with get_db_session() as session:
        if template.metadata.pms_capability_requirements:
            if not data.location_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="location_id is required to verify PMS capability for this template",
                )
            institution, location = await _resolve_institution_location(
                current_user,
                session,
                data.location_id,
            )
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
            location_id=data.location_id,
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
        return WorkflowResponse.from_model(wf)


async def _resolve_institution_location(
    user: User,
    session,
    location_id: str,
) -> tuple[Institution, InstitutionLocation]:
    if not user.institution_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No institution context")

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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found")

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
