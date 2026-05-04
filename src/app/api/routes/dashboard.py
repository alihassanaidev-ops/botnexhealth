"""
Dashboard summary route — institution-facing API for call volume metrics and queues.

Provides aggregate call statistics and the needs-callback queue used on
the institution dashboard page.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import and_, case, func, select

from src.app.api.deps import get_current_active_user, get_current_institution_admin
from src.app.api.rate_limit import RATE_READ, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.call import Call, CallStatus
from src.app.models.call_metrics_daily import CallMetricsDaily
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


class AggregateSummaryCards(BaseModel):
    total_calls_today: int
    total_calls_week: int
    total_calls_month: int
    total_calls_all_time: int
    appointments_booked_month: int
    new_patients_month: int
    booking_rate_month: float
    avg_call_duration_seconds: float
    open_callbacks: int


class LocationComparisonRow(BaseModel):
    location_id: str
    location_name: str
    location_slug: str
    status: str
    calls_today: int
    calls_this_month: int
    appointments_booked_month: int
    new_patients_month: int
    booking_rate_month: float
    avg_call_duration_seconds: float
    open_callbacks: int


class AggregateDashboardResponse(BaseModel):
    summary: AggregateSummaryCards
    tag_distribution: list[TagCount]
    clinic_comparison: list[LocationComparisonRow]
    as_of: str


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


def _count_calls(*conditions):
    """Return a COUNT(calls.id) expression with optional filtered conditions.

    Used only for "today"-scoped + open-callback live queries. Historical
    aggregates (week / month / all-time / per-location grouping) flow
    through ``call_metrics_daily`` via :func:`_load_rollup_volume_metrics`
    — it issues a tiny SUM over a few hundred rollup rows instead of
    scanning the full ``calls`` table.
    """
    count_expr = func.count(Call.id)
    if conditions:
        return count_expr.filter(*conditions)
    return count_expr


def _rollup_location_filter(location_id: str | None):
    """Map a ``calls.location_id`` filter to the rollup's NOT-NULL column.

    ``calls.location_id IS NULL`` rows are recomputed under the all-zero
    sentinel, so a None filter means "any location" (no predicate); a
    real location id matches that location's rollup rows; the sentinel
    is exposed as well for super-admin-style "uncategorised" queries.
    """
    if location_id is None:
        return None
    return CallMetricsDaily.location_id == location_id


async def _load_rollup_volume_metrics(
    session,
    *,
    institution_id: str,
    today,
    week_start,
    month_start,
    location_filter,
):
    """Pull (week, month, all_time) totals from ``call_metrics_daily``.

    Excludes today (rollup lags ~5 minutes; the live ``calls`` query
    handles today). Returns a single row of three integers — one DB
    round-trip per dashboard load, regardless of the underlying ``calls``
    table size.
    """
    rollup_filters = [
        CallMetricsDaily.institution_id == institution_id,
        CallMetricsDaily.call_date < today,
    ]
    if location_filter is not None:
        rollup_filters.append(location_filter)

    return (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (CallMetricsDaily.call_date >= week_start, CallMetricsDaily.total_calls),
                            else_=0,
                        )
                    ),
                    0,
                ).label("week_total"),
                func.coalesce(
                    func.sum(
                        case(
                            (CallMetricsDaily.call_date >= month_start, CallMetricsDaily.total_calls),
                            else_=0,
                        )
                    ),
                    0,
                ).label("month_total"),
                func.coalesce(func.sum(CallMetricsDaily.total_calls), 0).label("all_time_total"),
            ).where(*rollup_filters)
        )
    ).one()


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=DashboardSummary)
@limiter.limit(RATE_READ)
async def get_dashboard_summary(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_slug: str | None = Query(None),
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
        # Resolved location id for the rollup-side queries — None means
        # "no per-location filter" (institution-wide view).
        rollup_location_id: str | None = None
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
            if not location:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid location scope")
            extra_conditions.append(Call.location_id == location.id)
            rollup_location_id = str(location.id)
        elif current_user.role == UserRole.INSTITUTION_ADMIN.value and location_slug:
            location_result = await session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.slug == location_slug,
                    InstitutionLocation.institution_id == institution_id,
                )
            )
            scoped_location = location_result.scalar_one_or_none()
            if not scoped_location:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
            extra_conditions.append(Call.location_id == scoped_location.id)
            rollup_location_id = str(scoped_location.id)

        # ── Volume counts ─────────────────────────────────────────────────
        # Today: live count from ``calls`` (one tight index range scan
        # under the (institution_id, call_date) covering index — small
        # subset, zero growth concern).
        today_count = (
            await session.execute(
                select(func.count(Call.id)).where(
                    Call.institution_id == institution_id,
                    Call.call_date == today,
                    *extra_conditions,
                )
            )
        ).scalar_one() or 0

        # Week / month / all-time: SUM from the rollup. Excludes today;
        # the rollup recompute lags ~5 min so today is added live above.
        # Without the rollup these would be aggregate scans growing with
        # the ``calls`` table — the dashboard's worst-scaling query.
        rollup_row = await _load_rollup_volume_metrics(
            session,
            institution_id=institution_id,
            today=today,
            week_start=week_start,
            month_start=month_start,
            location_filter=_rollup_location_filter(rollup_location_id),
        )

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

        # Stitch rollup totals (date < today) with the live ``today_count``.
        # Today is bracketed in [week_start, today] and [month_start, today]
        # so it must always be added to the bucketed sums.
        week_total = int(rollup_row.week_total or 0) + today_count
        month_total = int(rollup_row.month_total or 0) + today_count
        all_time_total = int(rollup_row.all_time_total or 0) + today_count

        response = DashboardSummary(
            call_volume=CallVolume(
                today=today_count,
                this_week=week_total,
                this_month=month_total,
                all_time=all_time_total,
            ),
            tag_counts=tag_counts,
            callback_queue=callback_queue,
            as_of=now.isoformat(),
        )
        log_audit_background(
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
            action=AuditAction.VIEW_DASHBOARD,
            target_resource="dashboard:summary",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": institution_id,
                "location_id": current_user.location_id,
                "location_slug": location_slug,
            },
            institution_id=institution_id,
        )
        return response


@router.get("/aggregate", response_model=AggregateDashboardResponse)
@limiter.limit(RATE_READ)
async def get_aggregate_dashboard(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> AggregateDashboardResponse:
    """
    Institution-admin aggregate dashboard across all locations.

    Returns summary cards, tag distribution, and clinic comparison metrics.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
        )

    institution_id = current_user.institution_id
    now = datetime.now(timezone.utc)
    today = now.date()
    week_start = date.fromisocalendar(today.year, today.isocalendar().week, 1)
    month_start = today.replace(day=1)

    async with get_db_session() as session:
        locations = (
            await session.execute(
                select(InstitutionLocation).where(InstitutionLocation.institution_id == institution_id)
            )
        ).scalars().all()

        # Institution-wide summary cards
        summary_row = (
            await session.execute(
                select(
                    _count_calls(Call.call_date == today).label("total_calls_today"),
                    _count_calls(Call.call_date >= week_start).label("total_calls_week"),
                    _count_calls(Call.call_date >= month_start).label("total_calls_month"),
                    _count_calls().label("total_calls_all_time"),
                    _count_calls(
                        and_(
                            Call.call_status == CallStatus.APPOINTMENT_BOOKED.value,
                            Call.call_date >= month_start,
                        )
                    ).label("appointments_booked_month"),
                    _count_calls(
                        and_(
                            Call.is_new_patient.is_(True),
                            Call.call_date >= month_start,
                        )
                    ).label("new_patients_month"),
                    _count_calls(
                        and_(
                            Call.call_status == CallStatus.NEEDS_CALLBACK.value,
                            Call.callback_resolved.is_(False),
                        )
                    ).label("open_callbacks"),
                    func.coalesce(func.avg(Call.call_duration_seconds), 0.0).label("avg_call_duration_seconds"),
                ).where(Call.institution_id == institution_id)
            )
        ).one()
        total_calls_today = int(summary_row.total_calls_today or 0)
        total_calls_week = int(summary_row.total_calls_week or 0)
        total_calls_month = int(summary_row.total_calls_month or 0)
        total_calls_all_time = int(summary_row.total_calls_all_time or 0)
        appointments_booked_month = int(summary_row.appointments_booked_month or 0)
        new_patients_month = int(summary_row.new_patients_month or 0)
        open_callbacks = int(summary_row.open_callbacks or 0)
        booking_rate_month = (
            round((appointments_booked_month / total_calls_month) * 100, 2)
            if total_calls_month
            else 0.0
        )
        avg_call_duration_seconds = float(summary_row.avg_call_duration_seconds or 0.0)

        # Institution-wide tag distribution
        tag_rows = (
            await session.execute(
                select(Call.call_status, func.count(Call.id).label("cnt"))
                .where(
                    Call.institution_id == institution_id,
                    Call.call_status.isnot(None),
                )
                .group_by(Call.call_status)
                .order_by(func.count(Call.id).desc())
            )
        ).all()
        tag_distribution = [
            TagCount(
                tag=row.call_status,
                label=TAG_LABELS.get(row.call_status, row.call_status.replace("_", " ").title()),
                count=row.cnt,
            )
            for row in tag_rows
        ]

        # Per-location metrics grouped by Call.location_id (the authoritative
        # scope; agent_used is Retell metadata only and may be stale).
        metrics_rows = (
            await session.execute(
                select(
                    Call.location_id.label("location_id"),
                    func.sum(case((Call.call_date == today, 1), else_=0)).label("calls_today"),
                    func.sum(case((Call.call_date >= month_start, 1), else_=0)).label("calls_this_month"),
                    func.sum(
                        case(
                            (
                                and_(
                                    Call.call_status == CallStatus.APPOINTMENT_BOOKED.value,
                                    Call.call_date >= month_start,
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("appointments_booked_month"),
                    func.sum(
                        case(
                            (
                                and_(
                                    Call.is_new_patient.is_(True),
                                    Call.call_date >= month_start,
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("new_patients_month"),
                    func.sum(
                        case(
                            (
                                and_(
                                    Call.call_status == CallStatus.NEEDS_CALLBACK.value,
                                    Call.callback_resolved.is_(False),
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("open_callbacks"),
                    func.avg(Call.call_duration_seconds).label("avg_duration"),
                )
                .where(Call.institution_id == institution_id)
                .group_by(Call.location_id)
            )
        ).all()
        metrics_by_location = {row.location_id: row for row in metrics_rows}

        clinic_comparison: list[LocationComparisonRow] = []
        for loc in locations:
            loc_metrics = metrics_by_location.get(loc.id)
            calls_month = int(loc_metrics.calls_this_month or 0) if loc_metrics else 0
            bookings_month = int(loc_metrics.appointments_booked_month or 0) if loc_metrics else 0
            clinic_comparison.append(
                LocationComparisonRow(
                    location_id=str(loc.id),
                    location_name=loc.name,
                    location_slug=loc.slug,
                    status="Active" if loc.is_active else "Inactive",
                    calls_today=int(loc_metrics.calls_today or 0) if loc_metrics else 0,
                    calls_this_month=calls_month,
                    appointments_booked_month=bookings_month,
                    new_patients_month=int(loc_metrics.new_patients_month or 0) if loc_metrics else 0,
                    booking_rate_month=round((bookings_month / calls_month) * 100, 2) if calls_month else 0.0,
                    avg_call_duration_seconds=round(float(loc_metrics.avg_duration or 0), 2) if loc_metrics else 0.0,
                    open_callbacks=int(loc_metrics.open_callbacks or 0) if loc_metrics else 0,
                )
            )

        clinic_comparison.sort(key=lambda row: row.calls_this_month, reverse=True)

        response = AggregateDashboardResponse(
            summary=AggregateSummaryCards(
                total_calls_today=total_calls_today,
                total_calls_week=total_calls_week,
                total_calls_month=total_calls_month,
                total_calls_all_time=total_calls_all_time,
                appointments_booked_month=appointments_booked_month,
                new_patients_month=new_patients_month,
                booking_rate_month=booking_rate_month,
                avg_call_duration_seconds=round(float(avg_call_duration_seconds), 2),
                open_callbacks=open_callbacks,
            ),
            tag_distribution=tag_distribution,
            clinic_comparison=clinic_comparison,
            as_of=now.isoformat(),
        )
        log_audit_background(
            actor=AuditActor.ADMIN,
            user_id=str(current_user.id),
            action=AuditAction.VIEW_DASHBOARD,
            target_resource="dashboard:aggregate",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "actor_role": current_user.role,
                "institution_id": institution_id,
                "location_count": len(clinic_comparison),
            },
            institution_id=institution_id,
        )
        return response
