"""
Calls routes — tenant-facing API for browsing call records.

All endpoints are tenant-scoped: a user can only see calls belonging
to their own tenant. PHI fields (transcript, recording_url) are
intentionally excluded from the list response.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, func, nullslast, select
from sqlalchemy.orm import selectinload

from src.app.api.deps import get_current_active_user
from src.app.api.rate_limit import RATE_READ, limiter
from src.app.database import get_db_session
from src.app.models.call import Call
from src.app.models.contact import Contact
from src.app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant/calls", tags=["Calls"])


# ── Response models ───────────────────────────────────────────────────────────


class ContactSummary(BaseModel):
    id: str
    full_name: str | None
    first_name: str | None
    last_name: str | None


class CallRecord(BaseModel):
    id: str
    retell_call_id: str | None
    call_direction: str | None
    call_status: str | None
    patient_status: str | None
    summary: str | None
    patient_sentiment: str | None
    next_action: str | None
    is_new_patient: bool
    is_complaint: bool
    is_insurance_billing: bool
    call_date: date | None
    call_time: str | None
    call_duration_seconds: int | None
    created_at: str
    contact: ContactSummary | None


class CallsListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CallRecord]


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.get("", response_model=CallsListResponse)
@limiter.limit(RATE_READ)
async def list_calls(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    call_status: str | None = Query(None, alias="status"),
    direction: str | None = Query(None),
    search: str | None = Query(None, description="Filter by contact name (partial, case-insensitive)"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
) -> CallsListResponse:
    """
    List calls for the authenticated tenant.

    Supports filtering by status, direction, date range, and contact name search.
    Returns paginated results ordered newest-first. PHI fields (transcript,
    recording_url) are excluded from this endpoint.
    """
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with a tenant",
        )

    async with get_db_session() as session:
        # ── Build shared WHERE conditions ─────────────────────────────────
        conditions = [Call.tenant_id == current_user.tenant_id]
        if call_status:
            conditions.append(Call.call_status == call_status)
        if direction:
            conditions.append(Call.call_direction == direction)
        if date_from:
            conditions.append(Call.call_date >= date_from)
        if date_to:
            conditions.append(Call.call_date <= date_to)

        need_join = bool(search)

        def _with_search(q):
            if need_join:
                q = q.join(Contact, Call.contact_id == Contact.id, isouter=True)
                q = q.where(Contact.full_name.ilike(f"%{search}%"))
            return q

        # ── Count ─────────────────────────────────────────────────────────
        total: int = (
            await session.execute(
                _with_search(select(func.count(Call.id)).where(*conditions))
            )
        ).scalar_one()

        # ── Fetch page ────────────────────────────────────────────────────
        rows = (
            await session.execute(
                _with_search(
                    select(Call)
                    .where(*conditions)
                    .options(selectinload(Call.contact))
                    .order_by(nullslast(desc(Call.call_date)), desc(Call.created_at))
                    .limit(limit)
                    .offset(offset)
                )
            )
        ).scalars().all()

        # ── Build response ────────────────────────────────────────────────
        items: list[CallRecord] = []
        for call in rows:
            contact_out: ContactSummary | None = None
            if call.contact:
                contact_out = ContactSummary(
                    id=call.contact.id,
                    full_name=call.contact.full_name,
                    first_name=call.contact.first_name,
                    last_name=call.contact.last_name,
                )
            items.append(
                CallRecord(
                    id=call.id,
                    retell_call_id=call.retell_call_id,
                    call_direction=call.call_direction,
                    call_status=call.call_status,
                    patient_status=call.patient_status,
                    summary=call.summary,
                    patient_sentiment=call.patient_sentiment,
                    next_action=call.next_action,
                    is_new_patient=call.is_new_patient,
                    is_complaint=call.is_complaint,
                    is_insurance_billing=call.is_insurance_billing,
                    call_date=call.call_date,
                    call_time=str(call.call_time) if call.call_time else None,
                    call_duration_seconds=call.call_duration_seconds,
                    created_at=call.created_at.isoformat(),
                    contact=contact_out,
                )
            )

        return CallsListResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=items,
        )
