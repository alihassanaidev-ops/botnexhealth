"""FastAPI routes for campaign template browsing and instantiation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.app.api.deps import (
    get_current_institution_or_location_admin,
    get_current_institution_user,
)
from src.app.api.routes.automation_workflows import WorkflowResponse
from src.app.database import get_db_session
from src.app.models.user import User
from src.app.services.automation.campaign_templates import (
    CampaignTemplate,
    get_template,
    instantiate_definition,
    list_templates,
)
from src.app.services.automation.definition_service import AutomationWorkflowDefinitionService

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
    def from_template(cls, t: CampaignTemplate) -> "CampaignTemplateResponse":
        return cls(
            id=t.id,
            name=t.name,
            description=t.description,
            trigger_type=t.trigger_type,
            definition=t.definition,
            tags=t.tags,
            category=t.category,
            metadata=asdict(t.metadata),
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
) -> list[CampaignTemplateResponse]:
    """List all available campaign templates."""
    return [CampaignTemplateResponse.from_template(t) for t in list_templates()]


@router.get("/{template_id}", response_model=CampaignTemplateResponse)
async def get_campaign_template(
    template_id: str,
    current_user: _InstitutionOrLocationAdmin,
) -> CampaignTemplateResponse:
    """Get a single campaign template by ID."""
    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return CampaignTemplateResponse.from_template(template)


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

    user_id = str(current_user.id) if getattr(current_user, "id", None) else None
    async with get_db_session() as session:
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
