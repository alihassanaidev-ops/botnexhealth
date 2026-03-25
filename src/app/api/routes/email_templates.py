"""
Email template routes — CRUD and preview for customizable notification emails.

Accessible to INSTITUTION_ADMIN users. Templates are scoped per institution.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from src.app.api.deps import get_current_institution_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.email_template import EmailTemplateType
from src.app.models.user import User
from src.app.services.email_template_service import (
    TEMPLATE_VARIABLES,
    EmailTemplateService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/email-templates", tags=["Email Templates"])

_VALID_TYPES = {t.value for t in EmailTemplateType}


# -- Response / request models -----------------------------------------------


class TemplateVariableInfo(BaseModel):
    key: str
    label: str
    sample: str


class EmailTemplateResponse(BaseModel):
    id: str
    institution_id: str
    template_type: str
    name: str
    subject_template: str
    html_body: str
    text_body: str
    is_active: bool
    created_at: str
    updated_at: str
    variables: list[TemplateVariableInfo]


class EmailTemplateListResponse(BaseModel):
    templates: list[EmailTemplateResponse]


class EmailTemplateUpdateRequest(BaseModel):
    name: str | None = None
    subject_template: str | None = None
    html_body: str | None = None
    text_body: str | None = None
    is_active: bool | None = None


class EmailTemplatePreviewRequest(BaseModel):
    subject_template: str
    html_body: str
    text_body: str
    template_type: str


class EmailTemplatePreviewResponse(BaseModel):
    subject: str
    html: str
    text: str


class EmailTemplateValidateRequest(BaseModel):
    template_str: str


class EmailTemplateValidateResponse(BaseModel):
    valid: bool
    error: str | None = None


# -- Helpers -----------------------------------------------------------------


def _variables_for_type(template_type: str) -> list[TemplateVariableInfo]:
    raw = TEMPLATE_VARIABLES.get(template_type, [])
    return [TemplateVariableInfo(**v) for v in raw]


def _to_response(t) -> EmailTemplateResponse:  # noqa: ANN001
    return EmailTemplateResponse(
        id=t.id,
        institution_id=t.institution_id,
        template_type=t.template_type,
        name=t.name,
        subject_template=t.subject_template,
        html_body=t.html_body,
        text_body=t.text_body,
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


# -- List all templates ------------------------------------------------------


@router.get("", response_model=EmailTemplateListResponse)
@limiter.limit(RATE_READ)
async def list_email_templates(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> EmailTemplateListResponse:
    """List all email templates for the institution. Seeds defaults if none exist."""
    institution_id = _require_institution(current_user)

    async with get_db_session() as session:
        svc = EmailTemplateService(session)
        templates = await svc.get_templates(institution_id)
        return EmailTemplateListResponse(
            templates=[_to_response(t) for t in templates],
        )


# -- Live preview (arbitrary content, not saved) ------------------------------


@router.post("/preview/live", response_model=EmailTemplatePreviewResponse)
@limiter.limit(RATE_WRITE)
async def live_preview_email_template(
    request: Request,
    body: EmailTemplatePreviewRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> EmailTemplatePreviewResponse:
    """Render arbitrary template content with sample data for live preview."""
    _validate_type(body.template_type)

    for field_name in ("subject_template", "html_body", "text_body"):
        error = EmailTemplateService.validate_template(getattr(body, field_name))
        if error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid Jinja2 syntax in {field_name}: {error}",
            )

    preview = EmailTemplateService.render_preview_raw(
        subject_template=body.subject_template,
        html_body=body.html_body,
        text_body=body.text_body,
        template_type=body.template_type,
    )
    return EmailTemplatePreviewResponse(**preview)


# -- Validate template syntax ------------------------------------------------


@router.post("/validate", response_model=EmailTemplateValidateResponse)
@limiter.limit(RATE_WRITE)
async def validate_template_syntax(
    request: Request,
    body: EmailTemplateValidateRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> EmailTemplateValidateResponse:
    """Validate Jinja2 template syntax."""
    error = EmailTemplateService.validate_template(body.template_str)
    return EmailTemplateValidateResponse(valid=error is None, error=error)


# ============================================================================
# PARAMETERIZED routes (/{template_type})
# ============================================================================


# -- Get single template by type --------------------------------------------


@router.get("/{template_type}", response_model=EmailTemplateResponse)
@limiter.limit(RATE_READ)
async def get_email_template(
    request: Request,
    template_type: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> EmailTemplateResponse:
    """Get a specific email template by type."""
    institution_id = _require_institution(current_user)
    _validate_type(template_type)

    async with get_db_session() as session:
        svc = EmailTemplateService(session)
        template = await svc.get_template_by_type(institution_id, template_type)
        if not template:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return _to_response(template)


# -- Update template ---------------------------------------------------------


@router.put("/{template_type}", response_model=EmailTemplateResponse)
@limiter.limit(RATE_WRITE)
async def update_email_template(
    request: Request,
    template_type: str,
    body: EmailTemplateUpdateRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> EmailTemplateResponse:
    """Update an email template's content or active status."""
    institution_id = _require_institution(current_user)
    _validate_type(template_type)

    for field_name in ("subject_template", "html_body", "text_body"):
        value = getattr(body, field_name, None)
        if value is not None:
            error = EmailTemplateService.validate_template(value)
            if error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid Jinja2 syntax in {field_name}: {error}",
                )

    async with get_db_session() as session:
        svc = EmailTemplateService(session)
        template = await svc.update_template(
            institution_id,
            template_type,
            name=body.name,
            subject_template=body.subject_template,
            html_body=body.html_body,
            text_body=body.text_body,
            is_active=body.is_active,
        )
        if not template:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return _to_response(template)


# -- Reset template to default -----------------------------------------------


@router.post("/{template_type}/reset", response_model=EmailTemplateResponse)
@limiter.limit(RATE_WRITE)
async def reset_email_template(
    request: Request,
    template_type: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> EmailTemplateResponse:
    """Reset a template to its default content."""
    institution_id = _require_institution(current_user)
    _validate_type(template_type)

    async with get_db_session() as session:
        svc = EmailTemplateService(session)
        template = await svc.reset_template(institution_id, template_type)
        if not template:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return _to_response(template)


# -- Preview (saved template with sample data) --------------------------------


@router.get("/{template_type}/preview", response_model=EmailTemplatePreviewResponse)
@limiter.limit(RATE_READ)
async def preview_email_template(
    request: Request,
    template_type: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> EmailTemplatePreviewResponse:
    """Preview a saved template rendered with sample data."""
    institution_id = _require_institution(current_user)
    _validate_type(template_type)

    async with get_db_session() as session:
        svc = EmailTemplateService(session)
        preview = await svc.render_preview(institution_id, template_type)
        if not preview:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return EmailTemplatePreviewResponse(**preview)


# -- Get available variables for a template type -----------------------------


@router.get("/{template_type}/variables")
@limiter.limit(RATE_READ)
async def get_template_variables(
    request: Request,
    template_type: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> dict[str, Any]:
    """Get available template variables and their sample values."""
    _validate_type(template_type)
    return {"variables": _variables_for_type(template_type)}
