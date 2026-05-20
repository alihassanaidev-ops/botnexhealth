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

from src.app.api.deps import get_current_active_user
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.email_template import EmailTemplateType
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

    # Return all types, defaulting to enabled if no row exists
    items = [
        PreferenceItem(
            template_type=tt,
            is_enabled=prefs_by_type.get(tt, True),
        )
        for tt in _ALL_TYPES
    ]
    return PreferencesResponse(preferences=items)


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

    # Return the full preference state
    items = [
        PreferenceItem(
            template_type=tt,
            is_enabled=existing[tt].is_enabled if tt in existing else True,
        )
        for tt in _ALL_TYPES
    ]
    return PreferencesResponse(preferences=items)
