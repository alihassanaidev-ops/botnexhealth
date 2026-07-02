"""FastAPI routes for campaign template browsing and instantiation."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.app.api.deps import (
    get_current_institution_or_location_admin,
    get_current_institution_user,
)
from src.app.database import get_db_session
from src.app.models.user import User
from src.app.services.automation.campaign_templates import (
    CampaignTemplate,
    get_template,
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

    @classmethod
    def from_template(cls, t: CampaignTemplate) -> "CampaignTemplateResponse":
        return cls(
            id=t.id,
            name=t.name,
            description=t.description,
            trigger_type=t.trigger_type,
            definition=t.definition,
            tags=t.tags,
        )


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
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def instantiate_template(
    template_id: str,
    current_user: _InstitutionAdmin,
) -> dict:
    """Create a draft workflow from a campaign template."""
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No institution context")

    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    async with get_db_session() as session:
        svc = AutomationWorkflowDefinitionService(session)
        wf = await svc.create_draft(
            institution_id=str(current_user.institution_id),
            name=template.name,
            trigger_type=template.trigger_type,
            definition=template.definition,
        )

    return {
        "id": str(wf.id),
        "name": wf.name,
        "status": wf.status,
        "trigger_type": wf.trigger_type,
        "created_at": wf.created_at.isoformat(),
    }
