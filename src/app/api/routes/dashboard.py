"""
Dashboard summary route — institution-facing API for call volume metrics and queues.

Provides aggregate call statistics and the needs-callback queue used on
the institution dashboard page.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select

from src.app.api.deps import get_current_active_user
from src.app.api.rate_limit import RATE_READ, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.call import Call, CallStatus
from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.models.user import User, UserRole
from src.app.services.audit import log_audit_background

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/dashboard", tags=["Dashboard"])


# ── Response models ───────────────────────────────────────────────────────────


class CallVolume(BaseModel):
    today: int
    this_week: int
    this_month: int
    all_time: int


class TagCount(BaseModel):
    tag: str
    label: str
    count: int


class CallbackQueueItem(BaseModel):
    call_id: str
    contact_name: str | None
    call_date: date | None
    call_time: str | None
    call_duration_seconds: int | None
    summary: str | None
    next_action: str | None


class DashboardSummary(BaseModel):
    call_volume: CallVolume
    tag_counts: list[TagCount]
    callback_queue: list[CallbackQueueItem]   # unresolved needs_callback calls, oldest first
    as_of: str                                 # ISO timestamp of when this was computed


# ── Helpers ───────────────────────────────────────────────────────────────────

TAG_LABELS: dict[str, str] = {
    CallStatus.APPOINTMENT_BOOKED.value: "Appointment Booked",
    CallStatus.APPOINTMENT_RESCHEDULED.value: "Rescheduled",
    CallStatus.APPOINTMENT_CANCELLED.value: "Cancelled",
    CallStatus.EMERGENCY.value: "Emergency",
    CallStatus.COMPLAINT.value: "Complaint",
    CallStatus.NEEDS_CALLBACK.value: "Needs Callback",
    CallStatus.FAQ_HANDLED.value: "FAQ Handled",
    CallStatus.FINANCIAL_INQUIRY.value: "Financial Inquiry",
    CallStatus.TRANSFERRED.value: "Transferred",
    CallStatus.INSURANCE_VERIFIED.value: "Insurance Verified",
    CallStatus.INSURANCE_UNVERIFIED.value: "Insurance Unverified",
    CallStatus.NO_ACTION_NEEDED.value: "No Action Needed",
}


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=DashboardSummary)
@limiter.limit(RATE_READ)
async def get_dashboard_summary(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> DashboardSummary:
    """
    Return call volume metrics, per-tag counts, and the unresolved callback queue
    for the authenticated institution.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
        )

    institution_id = current_user.institution_id
    now = datetime.now(timezone.utc)
    today = now.date()

    # ISO week: Monday = start of week
    week_start = date.fromisocalendar(today.year, today.isocalendar().week, 1)
    month_start = today.replace(day=1)

    async with get_db_session() as session:
        extra_conditions = []
        if current_user.role in (UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value):
            if not current_user.location_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No location assignment")
            location_result = await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.id == current_user.location_id,
                    InstitutionLocation.institution_id == institution_id,
                )
            )
            location = location_result.scalar_one_or_none()
            if not location or not location.retell_agent_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid location scope")
            extra_conditions.append(Call.agent_used == location.retell_agent_id)

        # ── Volume counts ─────────────────────────────────────────────────
        base = select(func.count(Call.id)).where(Call.institution_id == institution_id, *extra_conditions)

        today_count: int = (
            await session.execute(base.where(Call.call_date == today))
        ).scalar_one()

        week_count: int = (
            await session.execute(base.where(Call.call_date >= week_start))
        ).scalar_one()

        month_count: int = (
            await session.execute(base.where(Call.call_date >= month_start))
        ).scalar_one()

        all_time_count: int = (
            await session.execute(base)
        ).scalar_one()

        # ── Tag counts (by primary call_status) ───────────────────────────
        tag_rows = (
            await session.execute(
                select(Call.call_status, func.count(Call.id).label("cnt"))
                .where(Call.institution_id == institution_id, Call.call_status.isnot(None), *extra_conditions)
                .group_by(Call.call_status)
                .order_by(func.count(Call.id).desc())
            )
        ).all()

        tag_counts = [
            TagCount(
                tag=row.call_status,
                label=TAG_LABELS.get(row.call_status, row.call_status.replace("_", " ").title()),
                count=row.cnt,
            )
            for row in tag_rows
        ]

        # ── Callback queue (unresolved needs_callback, oldest first) ──────
        callback_rows = (
            await session.execute(
                select(Call, Contact)
                .join(Contact, Call.contact_id == Contact.id, isouter=True)
                .where(
                    and_(
                        Call.institution_id == institution_id,
                        Call.call_status == CallStatus.NEEDS_CALLBACK.value,
                        Call.callback_resolved.is_(False),
                        *extra_conditions,
                    )
                )
                .order_by(Call.call_date.asc(), Call.created_at.asc())
                .limit(50)
            )
        ).all()

        callback_queue = [
            CallbackQueueItem(
                call_id=call.id,
                contact_name=contact.full_name if contact else None,
                call_date=call.call_date,
                call_time=str(call.call_time) if call.call_time else None,
                call_duration_seconds=call.call_duration_seconds,
                summary=call.summary,
                next_action=call.next_action,
            )
            for call, contact in callback_rows
        ]

        response = DashboardSummary(
            call_volume=CallVolume(
                today=today_count,
                this_week=week_count,
                this_month=month_count,
                all_time=all_time_count,
            ),
            tag_counts=tag_counts,
            callback_queue=callback_queue,
            as_of=now.isoformat(),
        )
        log_audit_background(
            actor=current_user.id,
            action=AuditAction.VIEW_DASHBOARD,
            target_resource="dashboard:summary",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": institution_id,
                "location_id": current_user.location_id,
            },
            institution_id=institution_id,
        )
        return response
