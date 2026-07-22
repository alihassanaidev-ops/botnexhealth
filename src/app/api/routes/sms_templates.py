"""
SMS template routes — CRUD and preview for customizable patient text messages.

Accessible to INSTITUTION_ADMIN users. Templates are scoped per institution and
mirror the email-template editor. The rendered body is populated from
authoritative structured appointment data at send time; the clinic-identity
prefix and CASL/TCPA opt-out footer are applied downstream by ``sms_privacy``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from src.app.api.deps import get_current_institution_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.sms_template import SmsTemplateType
from src.app.models.user import User
from src.app.services.sms_template_service import (
    TEMPLATE_VARIABLES,
    SmsTemplateService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/sms-templates", tags=["SMS Templates"])

_VALID_TYPES = {t.value for t in SmsTemplateType}


# -- Response / request models -----------------------------------------------


class TemplateVariableInfo(BaseModel):
    key: str
    label: str
    sample: str


class SmsTemplateResponse(BaseModel):
    id: str
    institution_id: str
    template_type: str
    name: str
    body: str
    is_active: bool
    created_at: str
    updated_at: str
    variables: list[TemplateVariableInfo]


class SmsTemplateListResponse(BaseModel):
    templates: list[SmsTemplateResponse]


class SmsTemplateUpdateRequest(BaseModel):
    name: str | None = None
    body: str | None = None
    is_active: bool | None = None


class SmsTemplatePreviewRequest(BaseModel):
    body: str
    template_type: str


class SmsTemplatePreviewResponse(BaseModel):
    body: str


class SmsTemplateValidateRequest(BaseModel):
    template_str: str


class SmsTemplateValidateResponse(BaseModel):
    valid: bool
    error: str | None = None


# -- Helpers -----------------------------------------------------------------


def _variables_for_type(template_type: str) -> list[TemplateVariableInfo]:
    raw = TEMPLATE_VARIABLES.get(template_type, [])
    return [TemplateVariableInfo(**v) for v in raw]


def _to_response(t) -> SmsTemplateResponse:  # noqa: ANN001
    return SmsTemplateResponse(
        id=t.id,
        institution_id=t.institution_id,
        template_type=t.template_type,
        name=t.name,
        body=t.body,
        is_active=t.is_active,
        created_at=t.created_at.isoformat(),
        updated_at=t.updated_at.isoformat(),
        variables=_variables_for_type(t.template_type),
    )


def _require_institution(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")
    return user.institution_id


def _validate_type(template_type: str) -> None:
    if template_type not in _VALID_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid template type. Valid types: {', '.join(sorted(_VALID_TYPES))}",
        )


# ============================================================================
# STATIC routes first (before parameterized /{template_type} routes)
# ============================================================================


@router.get("", response_model=SmsTemplateListResponse)
@limiter.limit(RATE_READ)
async def list_sms_templates(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> SmsTemplateListResponse:
    """List all SMS templates for the institution. Seeds defaults if none exist."""
    institution_id = _require_institution(current_user)

    async with get_db_session() as session:
        svc = SmsTemplateService(session)
        templates = await svc.get_templates(institution_id)
        return SmsTemplateListResponse(
            templates=[_to_response(t) for t in templates],
        )


@router.post("/preview/live", response_model=SmsTemplatePreviewResponse)
@limiter.limit(RATE_WRITE)
async def live_preview_sms_template(
    request: Request,
    body: SmsTemplatePreviewRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> SmsTemplatePreviewResponse:
    """Render arbitrary template content with sample data for live preview."""
    _validate_type(body.template_type)

    error = SmsTemplateService.validate_template(body.body)
    if error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid Jinja2 syntax in body: {error}",
        )

    preview = SmsTemplateService.render_preview_raw(
        body=body.body,
        template_type=body.template_type,
    )
    return SmsTemplatePreviewResponse(**preview)


@router.post("/validate", response_model=SmsTemplateValidateResponse)
@limiter.limit(RATE_WRITE)
async def validate_sms_template_syntax(
    request: Request,
    body: SmsTemplateValidateRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> SmsTemplateValidateResponse:
    """Validate Jinja2 template syntax."""
    error = SmsTemplateService.validate_template(body.template_str)
    return SmsTemplateValidateResponse(valid=error is None, error=error)


# ============================================================================
# PARAMETERIZED routes (/{template_type})
# ============================================================================


@router.get("/{template_type}", response_model=SmsTemplateResponse)
@limiter.limit(RATE_READ)
async def get_sms_template(
    request: Request,
    template_type: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> SmsTemplateResponse:
    """Get a specific SMS template by type."""
    institution_id = _require_institution(current_user)
    _validate_type(template_type)

    async with get_db_session() as session:
        svc = SmsTemplateService(session)
        template = await svc.get_template_by_type(institution_id, template_type)
        if not template:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return _to_response(template)


@router.put("/{template_type}", response_model=SmsTemplateResponse)
@limiter.limit(RATE_WRITE)
async def update_sms_template(
    request: Request,
    template_type: str,
    body: SmsTemplateUpdateRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> SmsTemplateResponse:
    """Update an SMS template's content or active status."""
    institution_id = _require_institution(current_user)
    _validate_type(template_type)

    if body.body is not None:
        error = SmsTemplateService.validate_template(body.body)
        if error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid Jinja2 syntax in body: {error}",
            )

    async with get_db_session() as session:
        svc = SmsTemplateService(session)
        template = await svc.update_template(
            institution_id,
            template_type,
            name=body.name,
            body=body.body,
            is_active=body.is_active,
        )
        if not template:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return _to_response(template)


@router.post("/{template_type}/reset", response_model=SmsTemplateResponse)
@limiter.limit(RATE_WRITE)
async def reset_sms_template(
    request: Request,
    template_type: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> SmsTemplateResponse:
    """Reset a template to its default content."""
    institution_id = _require_institution(current_user)
    _validate_type(template_type)

    async with get_db_session() as session:
        svc = SmsTemplateService(session)
        template = await svc.reset_template(institution_id, template_type)
        if not template:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return _to_response(template)


@router.get("/{template_type}/preview", response_model=SmsTemplatePreviewResponse)
@limiter.limit(RATE_READ)
async def preview_sms_template(
    request: Request,
    template_type: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> SmsTemplatePreviewResponse:
    """Preview a saved template rendered with sample data."""
    institution_id = _require_institution(current_user)
    _validate_type(template_type)

    async with get_db_session() as session:
        svc = SmsTemplateService(session)
        preview = await svc.render_preview(institution_id, template_type)
        if not preview:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return SmsTemplatePreviewResponse(**preview)


@router.get("/{template_type}/variables")
@limiter.limit(RATE_READ)
async def get_sms_template_variables(
    request: Request,
    template_type: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> dict[str, Any]:
    """Get available template variables and their sample values."""
    _validate_type(template_type)
    return {"variables": _variables_for_type(template_type)}
