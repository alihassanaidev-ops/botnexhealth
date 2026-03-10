"""
Notifications routes -- in-app notification API for authenticated users.

All endpoints are scoped by the authenticated user's institution_id AND user_id.
A user can only see and act on their own notifications.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from src.app.api.deps import get_current_active_user
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.user import User
from src.app.services.audit import log_audit_background
from src.app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/notifications", tags=["Notifications"])


# -- Response models (match frontend types exactly) ----------------------------


class NotificationItem(BaseModel):
    id: str
    user_id: str
    type: str
    title: str
    message: str
    is_read: bool
    created_at: str
    data: dict[str, Any] | None = None


class NotificationsListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[NotificationItem]


class NotificationUnreadCountResponse(BaseModel):
    total: int
    new_calls: int
    callbacks: int
    appointments: int
    urgent: int


class MarkAllReadResponse(BaseModel):
    updated: int


# -- Helpers -------------------------------------------------------------------


def _notification_to_item(n) -> NotificationItem:  # noqa: ANN001
    return NotificationItem(
        id=n.id,
        user_id=n.user_id,
        type=n.type,
        title=n.title,
        message=n.message,
        is_read=n.is_read,
        created_at=n.created_at.isoformat(),
        data=n.data,
    )


# -- List notifications -------------------------------------------------------


@router.get("", response_model=NotificationsListResponse)
@limiter.limit(RATE_READ)
async def list_notifications(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> NotificationsListResponse:
    """
    List notifications for the authenticated user.

    Returns paginated results ordered newest-first.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
        )

    async with get_db_session() as session:
        svc = NotificationService(session)
        items, total = await svc.get_notifications(
            user_id=current_user.id,
            limit=limit,
            offset=offset,
        )

        response = NotificationsListResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=[_notification_to_item(n) for n in items],
        )
        log_audit_background(
            actor=current_user.id,
            action=AuditAction.VIEW_CALLS,
            target_resource="notifications:list",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "result_count": len(response.items),
            },
            institution_id=current_user.institution_id,
        )
        return response


# -- Unread count --------------------------------------------------------------


@router.get("/unread-count", response_model=NotificationUnreadCountResponse)
@limiter.limit(RATE_READ)
async def get_unread_count(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> NotificationUnreadCountResponse:
    """
    Get unread notification counts broken down by type.

    Returns totals matching the frontend ``NotificationUnreadCount`` interface.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
        )

    async with get_db_session() as session:
        svc = NotificationService(session)
        counts = await svc.get_unread_counts(user_id=current_user.id)
        return NotificationUnreadCountResponse(**counts)


# -- Mark single as read ------------------------------------------------------


@router.patch("/{notification_id}/read")
@limiter.limit(RATE_WRITE)
async def mark_notification_as_read(
    request: Request,
    notification_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, str]:
    """
    Mark a single notification as read.

    Only the notification's owner can mark it. Returns 404 if not found
    or not owned by the current user.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
        )

    async with get_db_session() as session:
        svc = NotificationService(session)
        success = await svc.mark_as_read(
            user_id=current_user.id,
            notification_id=notification_id,
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found",
            )

        log_audit_background(
            actor=current_user.id,
            action=AuditAction.LOCATION_UPDATE,
            target_resource=f"notification:{notification_id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "action": "mark_read",
            },
            institution_id=current_user.institution_id,
        )
        return {"status": "ok"}


# -- Mark all as read ----------------------------------------------------------


@router.post("/mark-all-read", response_model=MarkAllReadResponse)
@limiter.limit(RATE_WRITE)
async def mark_all_notifications_as_read(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> MarkAllReadResponse:
    """
    Mark all unread notifications for the authenticated user as read.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
        )

    async with get_db_session() as session:
        svc = NotificationService(session)
        count = await svc.mark_all_as_read(user_id=current_user.id)

        log_audit_background(
            actor=current_user.id,
            action=AuditAction.LOCATION_UPDATE,
            target_resource="notifications:mark-all-read",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "updated_count": count,
            },
            institution_id=current_user.institution_id,
        )
        return MarkAllReadResponse(updated=count)
