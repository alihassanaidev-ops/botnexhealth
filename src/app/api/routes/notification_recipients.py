"""
External notification recipient routes — manage external emails that receive notifications.

Institution admins can add, update, and remove external email addresses
and configure which notification types each address receives.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.app.api.deps import get_current_institution_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.email_template import EmailTemplateType
from src.app.models.external_notification_recipient import ExternalNotificationRecipient
from src.app.models.user import User
from src.app.services.audit import log_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/notification-recipients", tags=["Notification Recipients"])

_VALID_TYPES = {t.value for t in EmailTemplateType}
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


# -- Request / response models -----------------------------------------------


class ExternalRecipientResponse(BaseModel):
    id: str
    email: str
    template_type: str
    is_active: bool
    created_at: str


class ExternalRecipientListResponse(BaseModel):
    recipients: list[ExternalRecipientResponse]


class AddExternalRecipientRequest(BaseModel):
    email: str = Field(..., max_length=255)
    template_types: list[str] = Field(..., min_length=1)


class UpdateExternalRecipientRequest(BaseModel):
    is_active: bool | None = None


# -- Helpers -----------------------------------------------------------------


def _to_response(r: ExternalNotificationRecipient) -> ExternalRecipientResponse:
    return ExternalRecipientResponse(
        id=r.id,
        email=r.email,
        template_type=r.template_type,
        is_active=r.is_active,
        created_at=r.created_at.isoformat(),
    )


def _require_institution(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")
    return user.institution_id


# -- List --------------------------------------------------------------------


@router.get("", response_model=ExternalRecipientListResponse)
@limiter.limit(RATE_READ)
async def list_external_recipients(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> ExternalRecipientListResponse:
    """List all external notification recipients for the institution."""
    institution_id = _require_institution(current_user)

    async with get_db_session() as session:
        result = await session.execute(
            select(ExternalNotificationRecipient)
            .where(ExternalNotificationRecipient.institution_id == institution_id)
            .order_by(ExternalNotificationRecipient.email, ExternalNotificationRecipient.template_type)
        )
        rows = list(result.scalars().all())
        return ExternalRecipientListResponse(recipients=[_to_response(r) for r in rows])


# -- Add --------------------------------------------------------------------


@router.post("", response_model=ExternalRecipientListResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_WRITE)
async def add_external_recipient(
    request: Request,
    body: AddExternalRecipientRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> ExternalRecipientListResponse:
    """Add an external email as a notification recipient for one or more template types."""
    institution_id = _require_institution(current_user)

    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid email address")

    for tt in body.template_types:
        if tt not in _VALID_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid template type: {tt}. Valid types: {', '.join(sorted(_VALID_TYPES))}",
            )

    async with get_db_session() as session:
        created: list[ExternalNotificationRecipient] = []
        for tt in body.template_types:
            # Check for duplicates
            existing = await session.execute(
                select(ExternalNotificationRecipient).where(
                    ExternalNotificationRecipient.institution_id == institution_id,
                    ExternalNotificationRecipient.email == email,
                    ExternalNotificationRecipient.template_type == tt,
                )
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Recipient {email} already exists for {tt}",
                )

            recipient = ExternalNotificationRecipient(
                id=str(uuid4()),
                institution_id=institution_id,
                email=email,
                template_type=tt,
                is_active=True,
            )
            session.add(recipient)
            created.append(recipient)

        await session.flush()

        # Audit external recipient additions explicitly. Forwarding clinic
        # email content to addresses outside our BAA scope is a high-risk
        # action and the audit log must record who added each recipient
        # and what they're subscribed to.
        for recipient in created:
            await log_audit(
                actor=AuditActor.ADMIN,
                action=AuditAction.EXTERNAL_RECIPIENT_ADD,
                target_resource=f"external_recipient:{recipient.id}",
                outcome=AuditOutcome.SUCCESS,
                metadata={
                    "actor_role": current_user.role,
                    "email": recipient.email,
                    "template_type": recipient.template_type,
                },
                institution_id=institution_id,
                user_id=str(current_user.id),
            )

        return ExternalRecipientListResponse(recipients=[_to_response(r) for r in created])


# -- Update ------------------------------------------------------------------


@router.put("/{recipient_id}", response_model=ExternalRecipientResponse)
@limiter.limit(RATE_WRITE)
async def update_external_recipient(
    request: Request,
    recipient_id: str,
    body: UpdateExternalRecipientRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> ExternalRecipientResponse:
    """Update an external recipient (toggle active status)."""
    institution_id = _require_institution(current_user)

    async with get_db_session() as session:
        result = await session.execute(
            select(ExternalNotificationRecipient).where(
                ExternalNotificationRecipient.id == recipient_id,
                ExternalNotificationRecipient.institution_id == institution_id,
            )
        )
        recipient = result.scalar_one_or_none()
        if not recipient:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipient not found")

        was_active = recipient.is_active
        if body.is_active is not None:
            recipient.is_active = body.is_active

        session.add(recipient)
        await session.flush()

        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.EXTERNAL_RECIPIENT_UPDATE,
            target_resource=f"external_recipient:{recipient.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "email": recipient.email,
                "template_type": recipient.template_type,
                "was_active": was_active,
                "is_active": recipient.is_active,
            },
            institution_id=institution_id,
            user_id=str(current_user.id),
        )
        return _to_response(recipient)


# -- Delete ------------------------------------------------------------------


@router.delete("/{recipient_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_WRITE)
async def delete_external_recipient(
    request: Request,
    recipient_id: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> None:
    """Remove an external notification recipient."""
    institution_id = _require_institution(current_user)

    async with get_db_session() as session:
        target = (
            await session.execute(
                select(ExternalNotificationRecipient).where(
                    ExternalNotificationRecipient.id == recipient_id,
                    ExternalNotificationRecipient.institution_id == institution_id,
                )
            )
        ).scalar_one_or_none()
        if not target:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipient not found")

        target_email = target.email
        target_template = target.template_type
        await session.delete(target)

        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.EXTERNAL_RECIPIENT_REMOVE,
            target_resource=f"external_recipient:{recipient_id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "email": target_email,
                "template_type": target_template,
            },
            institution_id=institution_id,
            user_id=str(current_user.id),
        )
