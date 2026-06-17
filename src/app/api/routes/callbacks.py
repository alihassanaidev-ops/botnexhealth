"""
Callbacks routes — dedicated API for the callback queue.

Provides a paginated, filterable list of calls that need callbacks,
with support for resolved/unresolved filtering, date ranges, and search.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import selectinload

from src.app.api.deps import get_current_active_user
from src.app.api.rate_limit import RATE_READ, limiter
from src.app.api.routes.calls import ContactSummary, _location_agent_filter
from src.app.services.sms_privacy import mask_phone
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.call import Call, CallStatus
from src.app.models.contact import Contact
from src.app.models.user import User
from src.app.services.audit import log_audit_background

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/callbacks", tags=["Callbacks"])


# ── Response models ───────────────────────────────────────────────────────────


class CallbackItem(BaseModel):
    call_id: str
    contact_name: str | None
    call_date: date | None
    call_time: str | None
    call_duration_seconds: int | None
    summary: str | None
    next_action: str | None
    callback_resolved: bool
    callback_resolved_at: str | None
    callback_note: str | None
    preferred_callback_datetime: str | None
    created_at: str
    contact: ContactSummary | None
    # Masked callback number; full value via POST /institution/calls/{id}/reveal/phone.
    phone_masked: str | None = None
    phone_reveal_available: bool = False


class CallbacksListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CallbackItem]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _call_to_callback_item(call: Call) -> CallbackItem:
    contact_out: ContactSummary | None = None
    phone_masked: str | None = None
    phone_reveal_available = False
    if call.contact:
        contact_out = ContactSummary(
            id=call.contact.id,
            full_name=call.contact.full_name,
            first_name=call.contact.first_name,
            last_name=call.contact.last_name,
        )
        phone_reveal_available = call.contact.phone_encrypted is not None
        if phone_reveal_available:
            phone_masked = mask_phone(call.contact.phone)
    return CallbackItem(
        call_id=call.id,
        contact_name=call.contact.full_name if call.contact else None,
        call_date=call.call_date,
        call_time=str(call.call_time) if call.call_time else None,
        call_duration_seconds=call.call_duration_seconds,
        summary=call.summary,
        next_action=call.next_action,
        callback_resolved=call.callback_resolved,
        callback_resolved_at=call.callback_resolved_at.isoformat() if call.callback_resolved_at else None,
        callback_note=call.callback_note,
        preferred_callback_datetime=call.preferred_callback_datetime.isoformat() if call.preferred_callback_datetime else None,
        created_at=call.created_at.isoformat(),
        contact=contact_out,
        phone_masked=phone_masked,
        phone_reveal_available=phone_reveal_available,
    )


# ── List callbacks ────────────────────────────────────────────────────────────


@router.get("", response_model=CallbacksListResponse)
@limiter.limit(RATE_READ)
async def list_callbacks(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    resolved: bool | None = Query(None, description="Filter: True=resolved, False=unresolved, None=all"),
    search: str | None = Query(None, description="Filter by contact name (partial, case-insensitive)"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    sort: str = Query("oldest", description="Sort order: 'oldest' or 'newest'"),
) -> CallbacksListResponse:
    """
    List callback calls for the authenticated institution.

    Only returns calls with status 'needs_callback'. Supports filtering by
    resolved status, date range, contact name search, and sort order.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
        )

    async with get_db_session() as session:
        conditions = [
            Call.institution_id == current_user.institution_id,
            or_(
                Call.call_status == CallStatus.NEEDS_CALLBACK.value,
                Call.call_tags.ilike(f"%{CallStatus.NEEDS_CALLBACK.value}%"),
            ),
        ]

        location_agent_id = await _location_agent_filter(session, current_user)
        if location_agent_id:
            conditions.append(Call.location_id == location_agent_id)

        if resolved is not None:
            conditions.append(Call.callback_resolved.is_(resolved))

        if date_from:
            conditions.append(Call.call_date >= date_from)
        if date_to:
            conditions.append(Call.call_date <= date_to)

        def _with_name_search(q):
            if not search:
                return q
            q = q.join(Contact, Call.contact_id == Contact.id, isouter=True)
            q = q.where(Contact.full_name.ilike(f"%{search}%"))
            return q

        total: int = (
            await session.execute(
                _with_name_search(select(func.count(Call.id)).where(*conditions))
            )
        ).scalar_one()

        # Sort order
        if sort == "newest":
            order = [desc(Call.call_date), desc(Call.created_at)]
        else:
            order = [Call.call_date.asc(), Call.created_at.asc()]

        rows = (
            await session.execute(
                _with_name_search(
                    select(Call)
                    .where(*conditions)
                    .options(selectinload(Call.contact))
                    .order_by(*order)
                    .limit(limit)
                    .offset(offset)
                )
            )
        ).scalars().all()

        response = CallbacksListResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=[_call_to_callback_item(c) for c in rows],
        )

        log_audit_background(
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
            action=AuditAction.VIEW_CALLS,
            target_resource="callbacks:list",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": current_user.institution_id,
                "location_id": current_user.location_id,
                "result_count": len(response.items),
                "resolved_filter": resolved,
            },
            institution_id=current_user.institution_id,
        )
        return response
