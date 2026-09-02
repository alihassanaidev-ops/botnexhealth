"""Institution/location controls for the managed inbound email channel."""

from __future__ import annotations

from email.utils import parseaddr
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from src.app.api.deps import (
    get_current_institution_location_or_super_admin,
    resolve_target_institution,
)
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.user import User, UserRole
from src.app.services.audit_decorator import audit
from src.app.services.email.inbox_settings_service import (
    EffectiveInboxSettings,
    InboxSettingsService,
)

router = APIRouter(prefix="/institution/email-inbox-settings", tags=["Email Inbox"])


def _scope_for_user(
    user: User, institution_id: str | None, location_id: str | None
) -> tuple[str, str | None]:
    if user.role == UserRole.LOCATION_ADMIN.value:
        if not user.institution_id or not user.location_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Location administrator is missing tenant scope",
            )
        if institution_id and str(institution_id) != str(user.institution_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot act on another institution",
            )
        return str(user.institution_id), str(user.location_id)
    return resolve_target_institution(user, institution_id), location_id


class InboxSettingsResponse(BaseModel):
    institution_id: str
    location_id: str | None
    is_enabled: bool
    allow_new_contacts: bool
    stop_automation_on_reply: bool
    forward_to: str | None
    inherited: bool
    platform_ready: bool
    inbound_domain: str | None
    inbox_address: str | None


class InboxSettingsUpdate(BaseModel):
    is_enabled: bool = False
    allow_new_contacts: bool = False
    stop_automation_on_reply: bool = True
    forward_to: str | None = Field(default=None, max_length=320)

    @field_validator("forward_to")
    @classmethod
    def valid_forward_to(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        cleaned = value.strip()
        _name, parsed = parseaddr(cleaned)
        if parsed != cleaned or "@" not in parsed or parsed.startswith("@") or parsed.endswith("@"):
            raise ValueError("Enter a valid forwarding email address")
        return cleaned


def _response(value: EffectiveInboxSettings) -> InboxSettingsResponse:
    address = value.inbox_address
    return InboxSettingsResponse(
        institution_id=value.institution_id,
        location_id=value.location_id,
        is_enabled=value.is_enabled,
        allow_new_contacts=value.allow_new_contacts,
        stop_automation_on_reply=value.stop_automation_on_reply,
        forward_to=value.forward_to,
        inherited=value.inherited,
        platform_ready=value.platform_ready,
        inbound_domain=address.split("@", 1)[1] if address else None,
        inbox_address=address,
    )


@router.get("", response_model=InboxSettingsResponse)
@limiter.limit(RATE_READ)
async def get_email_inbox_settings(
    request: Request,
    current_user: Annotated[
        User, Depends(get_current_institution_location_or_super_admin)
    ],
    institution_id: str | None = None,
    location_id: str | None = None,
) -> InboxSettingsResponse:
    institution_id, location_id = _scope_for_user(
        current_user, institution_id, location_id
    )
    async with get_db_session() as session:
        return _response(await InboxSettingsService(session).get(institution_id, location_id))


@router.put("", response_model=InboxSettingsResponse)
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.EMAIL_INBOX_SETTINGS_UPDATE,
    resource=lambda request, location_id=None, **_: f"email_inbox:{location_id or 'default'}",
    actor=AuditActor.ADMIN,
)
async def update_email_inbox_settings(
    request: Request,
    body: InboxSettingsUpdate,
    current_user: Annotated[
        User, Depends(get_current_institution_location_or_super_admin)
    ],
    institution_id: str | None = None,
    location_id: str | None = None,
) -> InboxSettingsResponse:
    institution_id, location_id = _scope_for_user(
        current_user, institution_id, location_id
    )
    request.state.audit_institution_id = institution_id
    request.state.audit_location_id = location_id
    async with get_db_session() as session:
        try:
            value = await InboxSettingsService(session).upsert(
                institution_id,
                location_id,
                is_enabled=body.is_enabled,
                allow_new_contacts=body.allow_new_contacts,
                stop_automation_on_reply=body.stop_automation_on_reply,
                forward_to=body.forward_to,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return _response(value)
