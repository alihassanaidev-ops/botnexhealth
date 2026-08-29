"""No-PMS SMS notification recipient routes.

Institution and location admins can configure phone numbers that receive
automated no-PMS appointment-request SMS alerts. The routes are explicitly
blocked for PMS-backed institutions so NexHealth/GoTracker behavior is
unchanged.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from src.app.api.deps import get_current_institution_or_location_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.external_sms_notification_recipient import (
    ExternalSmsNotificationRecipient,
    StaffSmsAlertType,
)
from src.app.models.institution import Institution
from src.app.models.user import User, UserRole
from src.app.services.audit import log_audit
from src.app.services.sms_privacy import hash_phone, mask_phone, normalize_phone

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/institution/sms-notification-recipients",
    tags=["SMS Notification Recipients"],
)

_VALID_TYPES = {t.value for t in StaffSmsAlertType}


class SmsNotificationRecipientResponse(BaseModel):
    id: str
    phone_number_masked: str
    notification_type: str
    is_active: bool
    created_at: str
    # None = every location in the institution.
    location_id: str | None = None


class SmsNotificationRecipientListResponse(BaseModel):
    recipients: list[SmsNotificationRecipientResponse]


class AddSmsNotificationRecipientRequest(BaseModel):
    phone_number: str = Field(..., min_length=7, max_length=32)
    notification_type: str = StaffSmsAlertType.APPOINTMENT_REQUEST.value
    # Omit for institution-wide. A LOCATION_ADMIN's request is always forced to
    # their own location regardless of what they send.
    location_id: str | None = None


class UpdateSmsNotificationRecipientRequest(BaseModel):
    is_active: bool | None = None


def _to_response(
    recipient: ExternalSmsNotificationRecipient,
) -> SmsNotificationRecipientResponse:
    return SmsNotificationRecipientResponse(
        id=recipient.id,
        phone_number_masked=recipient.phone_number_masked,
        notification_type=recipient.notification_type,
        is_active=recipient.is_active,
        created_at=recipient.created_at.isoformat(),
        location_id=recipient.location_id,
    )


def _require_institution(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")
    return str(user.institution_id)


async def _require_no_pms(institution_id: str) -> None:
    async with get_db_session() as session:
        pms_type = (
            await session.execute(
                select(Institution.pms_type).where(Institution.id == institution_id)
            )
        ).scalar_one_or_none()
    if (pms_type or "nexhealth") != "none":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SMS preferences are only available for no-PMS institutions",
        )


def _manageable_scope(user: User) -> list:
    """Extra WHERE clauses limiting which recipients a user may modify."""
    if user.role == UserRole.LOCATION_ADMIN.value:
        return [ExternalSmsNotificationRecipient.location_id == str(user.location_id)]
    return []


def _scope_location_id(user: User, requested: str | None) -> str | None:
    """Resolve the location a recipient belongs to.

    A LOCATION_ADMIN may only manage recipients for their own location, so
    their choice is ignored and forced to it. An INSTITUTION_ADMIN may pick any
    location, or omit it for an institution-wide recipient. This matches how
    staff email recipients scope: institution admins see everything, location
    users only their own site.
    """
    if user.role == UserRole.LOCATION_ADMIN.value:
        if not user.location_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Location admin has no location assigned",
            )
        return str(user.location_id)
    return requested


def _validate_notification_type(notification_type: str) -> None:
    if notification_type not in _VALID_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid notification type: {notification_type}",
        )


@router.get("", response_model=SmsNotificationRecipientListResponse)
@limiter.limit(RATE_READ)
async def list_sms_notification_recipients(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
) -> SmsNotificationRecipientListResponse:
    institution_id = _require_institution(current_user)
    await _require_no_pms(institution_id)

    async with get_db_session() as session:
        conditions = [ExternalSmsNotificationRecipient.institution_id == institution_id]
        if current_user.role == UserRole.LOCATION_ADMIN.value:
            # Their own location's recipients, plus institution-wide ones they
            # can see but not manage away.
            conditions.append(
                or_(
                    ExternalSmsNotificationRecipient.location_id.is_(None),
                    ExternalSmsNotificationRecipient.location_id
                    == str(current_user.location_id),
                )
            )
        result = await session.execute(
            select(ExternalSmsNotificationRecipient)
            .where(*conditions)
            .order_by(ExternalSmsNotificationRecipient.created_at.desc())
        )
        rows = list(result.scalars().all())
    return SmsNotificationRecipientListResponse(recipients=[_to_response(r) for r in rows])


@router.post(
    "",
    response_model=SmsNotificationRecipientListResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(RATE_WRITE)
async def add_sms_notification_recipient(
    request: Request,
    body: AddSmsNotificationRecipientRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
) -> SmsNotificationRecipientListResponse:
    institution_id = _require_institution(current_user)
    await _require_no_pms(institution_id)
    _validate_notification_type(body.notification_type)

    location_id = _scope_location_id(current_user, body.location_id)
    normalized = normalize_phone(body.phone_number)
    phone_hash = hash_phone(normalized)
    if not normalized or not phone_hash:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid phone number",
        )

    async with get_db_session() as session:
        existing = (
            await session.execute(
                select(ExternalSmsNotificationRecipient).where(
                    ExternalSmsNotificationRecipient.institution_id == institution_id,
                    ExternalSmsNotificationRecipient.phone_number_hash == phone_hash,
                    ExternalSmsNotificationRecipient.notification_type
                    == body.notification_type,
                    ExternalSmsNotificationRecipient.location_id.is_(None)
                    if location_id is None
                    else ExternalSmsNotificationRecipient.location_id == location_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Recipient already exists for this notification type",
            )

        recipient = ExternalSmsNotificationRecipient(
            id=str(uuid4()),
            institution_id=institution_id,
            location_id=location_id,
            notification_type=body.notification_type,
            is_active=True,
        )
        recipient.phone_number = normalized
        session.add(recipient)
        await session.flush()

        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.EXTERNAL_RECIPIENT_ADD,
            target_resource=f"external_sms_recipient:{recipient.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "channel": "sms",
                "phone_number_masked": mask_phone(normalized),
                "phone_number_hash": phone_hash,
                "notification_type": recipient.notification_type,
            },
            institution_id=institution_id,
            user_id=str(current_user.id),
        )
        return SmsNotificationRecipientListResponse(recipients=[_to_response(recipient)])


@router.put("/{recipient_id}", response_model=SmsNotificationRecipientResponse)
@limiter.limit(RATE_WRITE)
async def update_sms_notification_recipient(
    request: Request,
    recipient_id: str,
    body: UpdateSmsNotificationRecipientRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
) -> SmsNotificationRecipientResponse:
    institution_id = _require_institution(current_user)
    await _require_no_pms(institution_id)

    async with get_db_session() as session:
        recipient = (
            await session.execute(
                select(ExternalSmsNotificationRecipient).where(
                    ExternalSmsNotificationRecipient.id == recipient_id,
                    ExternalSmsNotificationRecipient.institution_id == institution_id,
                    # A location admin may only act on their own location's
                    # recipients — institution-wide ones are visible to them but
                    # managed by an institution admin.
                    *_manageable_scope(current_user),
                )
            )
        ).scalar_one_or_none()
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
            target_resource=f"external_sms_recipient:{recipient.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "channel": "sms",
                "phone_number_masked": recipient.phone_number_masked,
                "phone_number_hash": recipient.phone_number_hash,
                "notification_type": recipient.notification_type,
                "was_active": was_active,
                "is_active": recipient.is_active,
            },
            institution_id=institution_id,
            user_id=str(current_user.id),
        )
        return _to_response(recipient)


@router.delete("/{recipient_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(RATE_WRITE)
async def delete_sms_notification_recipient(
    request: Request,
    recipient_id: str,
    current_user: Annotated[User, Depends(get_current_institution_or_location_admin)],
) -> None:
    institution_id = _require_institution(current_user)
    await _require_no_pms(institution_id)

    async with get_db_session() as session:
        recipient = (
            await session.execute(
                select(ExternalSmsNotificationRecipient).where(
                    ExternalSmsNotificationRecipient.id == recipient_id,
                    ExternalSmsNotificationRecipient.institution_id == institution_id,
                    # A location admin may only act on their own location's
                    # recipients — institution-wide ones are visible to them but
                    # managed by an institution admin.
                    *_manageable_scope(current_user),
                )
            )
        ).scalar_one_or_none()
        if not recipient:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipient not found")

        phone_masked = recipient.phone_number_masked
        phone_hash = recipient.phone_number_hash
        notification_type = recipient.notification_type
        await session.delete(recipient)

        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.EXTERNAL_RECIPIENT_REMOVE,
            target_resource=f"external_sms_recipient:{recipient_id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "channel": "sms",
                "phone_number_masked": phone_masked,
                "phone_number_hash": phone_hash,
                "notification_type": notification_type,
            },
            institution_id=institution_id,
            user_id=str(current_user.id),
        )
