"""Campaign email template routes — clinic-authored, reusable across campaigns.

Accessible to INSTITUTION_ADMIN users for their own institution, and to
SUPER_ADMIN for any institution named by ``institution_id``. Templates are
scoped per institution and isolated by RLS. Separate from
``/institution/email-templates``, which manages the five fixed system
notification templates.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.app.api.deps import (
    get_current_institution_or_super_admin,
    resolve_target_institution,
)
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.user import User
from src.app.services.campaign_email_template_service import (
    CampaignEmailTemplateError,
    CampaignEmailTemplateService,
    available_merge_fields,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/institution/campaign-email-templates",
    tags=["Campaign Email Templates"],
)


# -- Request / response models -----------------------------------------------


class CampaignEmailTemplateResponse(BaseModel):
    id: str
    key: str
    name: str
    subject_template: str
    html_body: str
    text_body: str
    is_active: bool


class CampaignEmailTemplateListResponse(BaseModel):
    templates: list[CampaignEmailTemplateResponse]


class CampaignEmailTemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    subject_template: str = Field(min_length=1, max_length=500)
    html_body: str = Field(min_length=1)
    text_body: str = Field(min_length=1)
    #: Optional — derived from ``name`` when omitted.
    key: str | None = Field(default=None, max_length=80)
    is_active: bool = True


class CampaignEmailTemplateUpdateRequest(BaseModel):
    """All fields optional; ``key`` is immutable because published workflow
    definitions reference it."""

    name: str | None = Field(default=None, max_length=255)
    subject_template: str | None = Field(default=None, max_length=500)
    html_body: str | None = None
    text_body: str | None = None
    is_active: bool | None = None


class CampaignEmailTemplatePreviewRequest(BaseModel):
    subject_template: str
    html_body: str
    text_body: str


class CampaignEmailTemplatePreviewResponse(BaseModel):
    subject: str
    html: str
    text: str


def _to_response(t) -> CampaignEmailTemplateResponse:  # noqa: ANN001
    return CampaignEmailTemplateResponse(
        id=str(t.id),
        key=t.key,
        name=t.name,
        subject_template=t.subject_template,
        html_body=t.html_body,
        text_body=t.text_body,
        is_active=t.is_active,
    )


def _bad_request(exc: CampaignEmailTemplateError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
    )


# ============================================================================
# STATIC routes first (before parameterized /{key} routes)
# ============================================================================


@router.get("", response_model=CampaignEmailTemplateListResponse)
@limiter.limit(RATE_READ)
async def list_campaign_email_templates(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
    institution_id: str | None = None,
    active_only: bool = False,
) -> CampaignEmailTemplateListResponse:
    institution_id = resolve_target_institution(current_user, institution_id)
    async with get_db_session() as session:
        templates = await CampaignEmailTemplateService(session).list_templates(
            institution_id, active_only=active_only
        )
        return CampaignEmailTemplateListResponse(
            templates=[_to_response(t) for t in templates]
        )


@router.post(
    "",
    response_model=CampaignEmailTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(RATE_WRITE)
async def create_campaign_email_template(
    request: Request,
    body: CampaignEmailTemplateCreateRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
    institution_id: str | None = None,
) -> CampaignEmailTemplateResponse:
    institution_id = resolve_target_institution(current_user, institution_id)
    async with get_db_session() as session:
        try:
            template = await CampaignEmailTemplateService(session).create(
                institution_id,
                name=body.name,
                subject_template=body.subject_template,
                html_body=body.html_body,
                text_body=body.text_body,
                key=body.key,
                is_active=body.is_active,
            )
        except CampaignEmailTemplateError as exc:
            raise _bad_request(exc) from exc
        return _to_response(template)


@router.get("/merge-fields")
@limiter.limit(RATE_READ)
async def get_campaign_template_merge_fields(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
    institution_id: str | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Merge fields an email template may use, for the editor's picker."""
    resolve_target_institution(current_user, institution_id)
    return {"fields": available_merge_fields()}


@router.post("/preview/live", response_model=CampaignEmailTemplatePreviewResponse)
@limiter.limit(RATE_WRITE)
async def live_preview_campaign_email_template(
    request: Request,
    body: CampaignEmailTemplatePreviewRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
    institution_id: str | None = None,
) -> CampaignEmailTemplatePreviewResponse:
    """Render unsaved content against sample merge values."""
    resolve_target_institution(current_user, institution_id)
    try:
        CampaignEmailTemplateService._validate_bodies(
            body.subject_template, body.html_body, body.text_body
        )
        preview = CampaignEmailTemplateService.render_preview_raw(
            subject_template=body.subject_template,
            html_body=body.html_body,
            text_body=body.text_body,
        )
    except CampaignEmailTemplateError as exc:
        raise _bad_request(exc) from exc
    return CampaignEmailTemplatePreviewResponse(**preview)


# ============================================================================
# PARAMETERIZED routes (/{key})
# ============================================================================


@router.get("/{key}", response_model=CampaignEmailTemplateResponse)
@limiter.limit(RATE_READ)
async def get_campaign_email_template(
    request: Request,
    key: str,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
    institution_id: str | None = None,
) -> CampaignEmailTemplateResponse:
    institution_id = resolve_target_institution(current_user, institution_id)
    async with get_db_session() as session:
        template = await CampaignEmailTemplateService(session).get_by_key(
            institution_id, key
        )
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
            )
        return _to_response(template)


@router.put("/{key}", response_model=CampaignEmailTemplateResponse)
@limiter.limit(RATE_WRITE)
async def update_campaign_email_template(
    request: Request,
    key: str,
    body: CampaignEmailTemplateUpdateRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
    institution_id: str | None = None,
) -> CampaignEmailTemplateResponse:
    institution_id = resolve_target_institution(current_user, institution_id)
    async with get_db_session() as session:
        try:
            template = await CampaignEmailTemplateService(session).update(
                institution_id,
                key,
                name=body.name,
                subject_template=body.subject_template,
                html_body=body.html_body,
                text_body=body.text_body,
                is_active=body.is_active,
            )
        except CampaignEmailTemplateError as exc:
            raise _bad_request(exc) from exc
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
            )
        return _to_response(template)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_WRITE)
async def delete_campaign_email_template(
    request: Request,
    key: str,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
    institution_id: str | None = None,
) -> None:
    institution_id = resolve_target_institution(current_user, institution_id)
    async with get_db_session() as session:
        deleted = await CampaignEmailTemplateService(session).delete(
            institution_id, key
        )
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
            )


@router.get("/{key}/preview", response_model=CampaignEmailTemplatePreviewResponse)
@limiter.limit(RATE_READ)
async def preview_campaign_email_template(
    request: Request,
    key: str,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
    institution_id: str | None = None,
) -> CampaignEmailTemplatePreviewResponse:
    institution_id = resolve_target_institution(current_user, institution_id)
    async with get_db_session() as session:
        preview = await CampaignEmailTemplateService(session).render_preview(
            institution_id, key
        )
        if preview is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
            )
        return CampaignEmailTemplatePreviewResponse(**preview)
