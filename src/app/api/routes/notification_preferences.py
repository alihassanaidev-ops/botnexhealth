"""
User email notification preferences — opt in/out of specific email types.

Any authenticated platform user can manage their own email notification
preferences. Uses an opt-out model: no rows = all enabled.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from src.app.api.deps import (
    get_current_active_user,
    get_current_institution_or_super_admin,
)
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.services.audit import log_audit
from src.app.models.email_template import EmailTemplateType
from src.app.models.institution import Institution
from src.app.models.user import User
from src.app.models.user_email_notification_preference import UserEmailNotificationPreference

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/notification-preferences", tags=["Notification Preferences"])

# Per-user opt-out preferences apply only to staff-facing alerts. The patient
# confirmation template is addressed to the patient, not a platform user, so
# it is excluded from this list.
_VALID_TYPES = {t.value for t in EmailTemplateType} - {
    EmailTemplateType.PATIENT_APPOINTMENT_CONFIRMATION.value
}
_ALL_TYPES = sorted(_VALID_TYPES)


# -- Request / response models -----------------------------------------------


class PreferenceItem(BaseModel):
    template_type: str
    is_enabled: bool


class PreferencesResponse(BaseModel):
    preferences: list[PreferenceItem]
    #: Institution-wide switch. False means nobody receives the automatic staff
    #: alerts regardless of their personal preferences above.
    institution_emails_enabled: bool = True


class UpdateInstitutionEmailsRequest(BaseModel):
    is_enabled: bool


class UpdatePreferencesRequest(BaseModel):
    preferences: list[PreferenceItem]


# -- Get preferences ---------------------------------------------------------


@router.get("", response_model=PreferencesResponse)
@limiter.limit(RATE_READ)
async def get_notification_preferences(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> PreferencesResponse:
    """Get the current user's email notification preferences.

    Returns all template types with their enabled status.
    Types without an explicit preference default to enabled.
    """
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    async with get_db_session() as session:
        result = await session.execute(
            select(UserEmailNotificationPreference).where(
                UserEmailNotificationPreference.user_id == current_user.id,
            )
        )
        prefs_by_type = {p.template_type: p.is_enabled for p in result.scalars().all()}
        institution = await session.get(Institution, current_user.institution_id)
        institution_enabled = (
            bool(institution.staff_notification_emails_enabled) if institution else True
        )

    # Return all types, defaulting to enabled if no row exists
    items = [
        PreferenceItem(
            template_type=tt,
            is_enabled=prefs_by_type.get(tt, True),
        )
        for tt in _ALL_TYPES
    ]
    return PreferencesResponse(
        preferences=items, institution_emails_enabled=institution_enabled
    )


# -- Update preferences ------------------------------------------------------


@router.put("", response_model=PreferencesResponse)
@limiter.limit(RATE_WRITE)
async def update_notification_preferences(
    request: Request,
    body: UpdatePreferencesRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> PreferencesResponse:
    """Bulk update the current user's email notification preferences.

    Uses upsert logic: creates preference rows if they don't exist,
    updates if they do.
    """
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    for pref in body.preferences:
        if pref.template_type not in _VALID_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid template type: {pref.template_type}. Valid types: {', '.join(_ALL_TYPES)}",
            )

    async with get_db_session() as session:
        # Load existing preferences for this user
        result = await session.execute(
            select(UserEmailNotificationPreference).where(
                UserEmailNotificationPreference.user_id == current_user.id,
            )
        )
        existing = {p.template_type: p for p in result.scalars().all()}

        for pref in body.preferences:
            if pref.template_type in existing:
                existing[pref.template_type].is_enabled = pref.is_enabled
                session.add(existing[pref.template_type])
            else:
                new_pref = UserEmailNotificationPreference(
                    id=str(uuid4()),
                    user_id=current_user.id,
                    template_type=pref.template_type,
                    is_enabled=pref.is_enabled,
                )
                session.add(new_pref)
                existing[pref.template_type] = new_pref

        await session.flush()
        institution = await session.get(Institution, current_user.institution_id)
        # Read rather than defaulted: returning True here would tell a page whose
        # institution has alerts switched off that they are on.
        institution_enabled = (
            bool(institution.staff_notification_emails_enabled) if institution else True
        )

    # Return the full preference state
    items = [
        PreferenceItem(
            template_type=tt,
            is_enabled=existing[tt].is_enabled if tt in existing else True,
        )
        for tt in _ALL_TYPES
    ]
    return PreferencesResponse(
        preferences=items, institution_emails_enabled=institution_enabled
    )


# -- Institution-wide switch -------------------------------------------------


@router.put("/institution", response_model=PreferencesResponse)
@limiter.limit(RATE_WRITE)
async def update_institution_notification_emails(
    request: Request,
    body: UpdateInstitutionEmailsRequest,
    current_user: Annotated[User, Depends(get_current_institution_or_super_admin)],
) -> PreferencesResponse:
    """Turn the automatic staff notification emails on or off for the practice.

    Admin-only, and deliberately separate from the per-user preferences above:
    this one decides whether anybody is emailed at all. Patient-facing mail is
    unaffected — that is a promise to the patient, not an internal alert.
    """
    if not current_user.institution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No institution")

    async with get_db_session() as session:
        institution = await session.get(Institution, current_user.institution_id)
        if institution is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found"
            )
        previous = bool(institution.staff_notification_emails_enabled)
        institution.staff_notification_emails_enabled = body.is_enabled
        session.add(institution)
        await session.flush()

        # Switching this off stops every staff alert for the practice, including
        # urgent ones. Who silenced them, and when, is exactly the question asked
        # after an urgent call goes unnoticed.
        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.INSTITUTION_UPDATE,
            target_resource=f"institution:{institution.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "field": "staff_notification_emails_enabled",
                "previous": previous,
                "current": body.is_enabled,
            },
            institution_id=str(institution.id),
            user_id=str(current_user.id),
        )

        result = await session.execute(
            select(UserEmailNotificationPreference).where(
                UserEmailNotificationPreference.user_id == current_user.id,
            )
        )
        prefs_by_type = {p.template_type: p.is_enabled for p in result.scalars().all()}

    logger.info(
        "Institution staff notification emails set: enabled=%s user=%s",
        body.is_enabled,
        current_user.id,
    )
    items = [
        PreferenceItem(template_type=tt, is_enabled=prefs_by_type.get(tt, True))
        for tt in _ALL_TYPES
    ]
    return PreferencesResponse(
        preferences=items, institution_emails_enabled=body.is_enabled
    )
