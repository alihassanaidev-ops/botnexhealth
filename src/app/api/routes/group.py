"""
Group oversight routes — read-only cross-institution dashboards for a DSO/group.

A `GROUP_ADMIN` user oversees one `InstitutionGroup`. These endpoints return
aggregate KPIs across the group's member institutions, sourced **only** from the
non-PHI `call_metrics_daily` rollup. The group role is wired to this router
alone; every institution/location/PHI dependency rejects it, so no per-patient
data, transcripts, recordings, or write paths are reachable.

Reads stay single-institution-per-request: member metrics are loaded in a loop
that sets the active-institution RLS context for each member, so Postgres RLS
remains a hard single-tenant failsafe. The only cross-institution step is summing
already-scoped per-member results in Python.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select, text

from src.app.api.deps import get_current_group_admin
from src.app.api.rate_limit import RATE_READ, limiter
from src.app.api.routes.dashboard import (
    TAG_LABELS,
    TagCount,
    _appt_booked_from_jsonb,
    _bucket_label,
    _bucket_start,
    _generate_buckets,
    _granularity_for_span,
    _range_rollup_kpis,
    _range_tag_distribution,
    _resolve_window,
    _rollup_location_filter,
)
from src.app.database import RlsContext, get_db_session, use_rls_context
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.call_metrics_daily import NULL_LOCATION_SENTINEL, CallMetricsDaily
from src.app.models.institution import Institution
from src.app.models.institution_group import InstitutionGroup
from src.app.models.institution_location import InstitutionLocation
from src.app.models.user import User, UserRole
from src.app.services.audit import log_audit_background

router = APIRouter(prefix="/group", tags=["Group"])


# ── Response models ───────────────────────────────────────────────────────────


class GroupMemberInfo(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool


class GroupMeResponse(BaseModel):
    id: str
    name: str
    slug: str
    members: list[GroupMemberInfo]


class InstitutionComparisonRow(BaseModel):
    """Per-institution KPIs over the selected date range (rollup-only, no PHI)."""

    institution_id: str
    institution_name: str
    institution_slug: str
    status: str
    total_calls: int
    appointments_booked: int
    new_patients: int
    booking_rate: float
    avg_call_duration_seconds: float


class GroupSummaryCards(BaseModel):
    institution_count: int
    total_calls: int
    appointments_booked: int
    new_patients: int
    booking_rate: float
    avg_call_duration_seconds: float


class GroupTrendPoint(BaseModel):
    bucket: str        # ISO date of the bucket start
    label: str         # human label (e.g. "Jun 3")
    total_calls: int
    appointments_booked: int
    new_patients: int


class GroupDashboardResponse(BaseModel):
    start_date: date
    end_date: date
    summary: GroupSummaryCards
    institution_comparison: list[InstitutionComparisonRow]
    trend: list[GroupTrendPoint]
    tag_distribution: list[TagCount]
    as_of: str


# ── Institution drill-in (one member practice + its locations) ──────────────────


class GroupLocationInfo(BaseModel):
    id: str
    name: str
    slug: str


class InstitutionKpis(BaseModel):
    total_calls: int
    appointments_booked: int
    new_patients: int
    booking_rate: float
    avg_call_duration_seconds: float


class LocationComparisonRow(BaseModel):
    location_id: str
    location_name: str
    total_calls: int
    appointments_booked: int
    new_patients: int
    booking_rate: float
    avg_call_duration_seconds: float


class GroupInstitutionDashboardResponse(BaseModel):
    institution_id: str
    institution_name: str
    start_date: date
    end_date: date
    selected_location_id: str | None
    locations: list[GroupLocationInfo]
    summary: InstitutionKpis
    trend: list[GroupTrendPoint]
    tag_distribution: list[TagCount]
    location_comparison: list[LocationComparisonRow]
    as_of: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _booking_rate(bookings: int, calls: int) -> float:
    return round((bookings / calls) * 100, 2) if calls else 0.0


def _avg_duration(total_seconds: int, total_calls: int) -> float:
    return round(total_seconds / total_calls, 2) if total_calls else 0.0


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/me", response_model=GroupMeResponse)
@limiter.limit(RATE_READ)
async def get_group_me(
    request: Request,
    current_user: Annotated[User, Depends(get_current_group_admin)],
) -> GroupMeResponse:
    """The caller's group profile + its member institutions."""
    async with get_db_session() as session:
        group = (
            await session.execute(
                select(InstitutionGroup).where(InstitutionGroup.id == current_user.group_id)
            )
        ).scalar_one_or_none()
        members = (
            await session.execute(
                select(Institution)
                .where(Institution.group_id == current_user.group_id)
                .order_by(Institution.name)
            )
        ).scalars().all()

    if group is None:
        # group_id present but group missing/deactivated — fail closed.
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    return GroupMeResponse(
        id=str(group.id),
        name=group.name,
        slug=group.slug,
        members=[
            GroupMemberInfo(id=str(m.id), name=m.name, slug=m.slug, is_active=m.is_active)
            for m in members
        ],
    )


@router.get("/dashboard", response_model=GroupDashboardResponse)
@limiter.limit(RATE_READ)
async def get_group_dashboard(
    request: Request,
    current_user: Annotated[User, Depends(get_current_group_admin)],
    start_date: date | None = Query(None, description="Inclusive range start (YYYY-MM-DD)"),
    end_date: date | None = Query(None, description="Inclusive range end (YYYY-MM-DD)"),
) -> GroupDashboardResponse:
    """Cross-institution KPIs + trend + tag mix over a date range (rollup-only, no PHI).

    Scales flat with group size: the GROUP_ADMIN's RLS context (group_id set,
    no institution scope) lets each query read the whole group's rollup at once,
    so this runs a CONSTANT number of queries regardless of how many practices
    the DSO has — no per-member loop. The rollup refreshes today every ~5 min,
    so today's row is included directly (no live ``calls`` overlay → no PHI).
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    start, end, _live_end_cap, _include_today = _resolve_window(start_date, end_date, today)
    end_cap = end + timedelta(days=1)

    granularity = _granularity_for_span((end - start).days + 1)
    buckets = _generate_buckets(start, end, granularity)
    bucket_expr = func.date_trunc(granularity, CallMetricsDaily.call_date)

    # Runs under the caller's request RLS context (group_id set). The group
    # membership RLS clause scopes every read below to this group's members.
    async with get_db_session() as session:
        members = (
            await session.execute(
                select(
                    Institution.id, Institution.name, Institution.slug, Institution.is_active
                )
                .where(
                    Institution.group_id == current_user.group_id,
                    Institution.is_active.is_(True),
                )
                .order_by(Institution.name)
            )
        ).all()
        # Explicit app-level scope to this group's members (defense-in-depth);
        # the call_metrics_daily group RLS clause is the failsafe behind it.
        member_ids = [str(m.id) for m in members]

        # One GROUP BY institution_id across all members.
        per_inst = (
            await session.execute(
                select(
                    CallMetricsDaily.institution_id.label("iid"),
                    func.coalesce(func.sum(CallMetricsDaily.total_calls), 0).label("total_calls"),
                    func.coalesce(func.sum(_appt_booked_from_jsonb()), 0).label("appointments"),
                    func.coalesce(func.sum(CallMetricsDaily.new_patient_calls), 0).label("new_patients"),
                    func.coalesce(func.sum(CallMetricsDaily.total_duration_seconds), 0).label("duration"),
                )
                .where(
                    CallMetricsDaily.institution_id.in_(member_ids),
                    CallMetricsDaily.call_date >= start,
                    CallMetricsDaily.call_date < end_cap,
                )
                .group_by(CallMetricsDaily.institution_id)
            )
        ).all() if member_ids else []

        # One GROUP BY bucket across all members.
        trend_rows = (
            await session.execute(
                select(
                    bucket_expr.label("bucket"),
                    func.coalesce(func.sum(CallMetricsDaily.total_calls), 0).label("total_calls"),
                    func.coalesce(func.sum(_appt_booked_from_jsonb()), 0).label("appointments"),
                    func.coalesce(func.sum(CallMetricsDaily.new_patient_calls), 0).label("new_patients"),
                )
                .where(
                    CallMetricsDaily.institution_id.in_(member_ids),
                    CallMetricsDaily.call_date >= start,
                    CallMetricsDaily.call_date < end_cap,
                )
                .group_by(bucket_expr)
            )
        ).all() if member_ids else []

        # One GROUP BY tag across all members (jsonb rollup sum; no PHI).
        tag_rows = (
            await session.execute(
                text(
                    """
                    SELECT t.key AS tag, SUM(t.value::int) AS cnt
                    FROM call_metrics_daily,
                         jsonb_each_text(call_metrics_daily.tag_counts) AS t(key, value)
                    WHERE call_metrics_daily.institution_id = ANY(:ids)
                      AND call_metrics_daily.call_date >= :start
                      AND call_metrics_daily.call_date < :end_cap
                    GROUP BY t.key
                    """
                ),
                {"ids": member_ids, "start": start, "end_cap": end_cap},
            )
        ).all() if member_ids else []

    agg = {str(r.iid): r for r in per_inst}
    comparison: list[InstitutionComparisonRow] = []
    sum_calls = sum_bookings = sum_new = sum_duration = 0
    for m in members:
        r = agg.get(str(m.id))
        calls = int(r.total_calls or 0) if r else 0
        bookings = int(r.appointments or 0) if r else 0
        new_patients = int(r.new_patients or 0) if r else 0
        duration = int(r.duration or 0) if r else 0
        comparison.append(
            InstitutionComparisonRow(
                institution_id=str(m.id),
                institution_name=m.name,
                institution_slug=m.slug,
                status="Active" if m.is_active else "Inactive",
                total_calls=calls,
                appointments_booked=bookings,
                new_patients=new_patients,
                booking_rate=_booking_rate(bookings, calls),
                avg_call_duration_seconds=_avg_duration(duration, calls),
            )
        )
        sum_calls += calls
        sum_bookings += bookings
        sum_new += new_patients
        sum_duration += duration
    comparison.sort(key=lambda r: r.total_calls, reverse=True)

    trend_totals: dict[date, dict[str, int]] = {
        b: {"total_calls": 0, "appointments": 0, "new_patients": 0} for b in buckets
    }
    for row in trend_rows:
        bucket = row.bucket.date() if hasattr(row.bucket, "date") else row.bucket
        slot = trend_totals.get(_bucket_start(bucket, granularity))
        if slot is not None:
            slot["total_calls"] += int(row.total_calls or 0)
            slot["appointments"] += int(row.appointments or 0)
            slot["new_patients"] += int(row.new_patients or 0)
    trend = [
        GroupTrendPoint(
            bucket=b.isoformat(),
            label=_bucket_label(b, granularity),
            total_calls=trend_totals[b]["total_calls"],
            appointments_booked=trend_totals[b]["appointments"],
            new_patients=trend_totals[b]["new_patients"],
        )
        for b in buckets
    ]

    tag_distribution = sorted(
        (
            TagCount(
                tag=row.tag,
                label=TAG_LABELS.get(row.tag, row.tag.replace("_", " ").title()),
                count=int(row.cnt or 0),
            )
            for row in tag_rows
            if row.tag is not None
        ),
        key=lambda x: x.count,
        reverse=True,
    )

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.VIEW_DASHBOARD,
        target_resource="group:dashboard",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "group_id": str(current_user.group_id),
            "institution_count": len(comparison),
            "range": f"{start.isoformat()}..{end.isoformat()}",
        },
    )

    return GroupDashboardResponse(
        start_date=start,
        end_date=end,
        summary=GroupSummaryCards(
            institution_count=len(comparison),
            total_calls=sum_calls,
            appointments_booked=sum_bookings,
            new_patients=sum_new,
            booking_rate=_booking_rate(sum_bookings, sum_calls),
            avg_call_duration_seconds=_avg_duration(sum_duration, sum_calls),
        ),
        institution_comparison=comparison,
        trend=trend,
        tag_distribution=tag_distribution,
        as_of=now.isoformat(),
    )


@router.get(
    "/institution/{institution_id}/dashboard",
    response_model=GroupInstitutionDashboardResponse,
)
@limiter.limit(RATE_READ)
async def get_group_institution_dashboard(
    request: Request,
    institution_id: str,
    current_user: Annotated[User, Depends(get_current_group_admin)],
    start_date: date | None = Query(None, description="Inclusive range start (YYYY-MM-DD)"),
    end_date: date | None = Query(None, description="Inclusive range end (YYYY-MM-DD)"),
    location_id: str | None = Query(None, description="Optional: scope to one location"),
) -> GroupInstitutionDashboardResponse:
    """One member practice's dashboard (KPIs + per-location comparison + trend).

    Mirrors the institution-admin location experience one level up: a group
    admin picks a member institution (and optionally a location) and sees its
    rollup-only metrics. Membership is verified; the RLS context is scoped to
    that single institution. No PHI — rollup tables + location names only.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    start, end, _live_end_cap, _include_today = _resolve_window(start_date, end_date, today)
    end_cap = end + timedelta(days=1)

    # Verify the institution is in the caller's group (under the group context,
    # the institutions policy only returns the caller's group members).
    async with get_db_session() as session:
        inst = (
            await session.execute(
                select(Institution.id, Institution.name).where(
                    Institution.id == institution_id,
                    Institution.group_id == current_user.group_id,
                )
            )
        ).first()
    if inst is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Institution not found in your group",
        )
    institution_name = inst.name

    granularity = _granularity_for_span((end - start).days + 1)
    buckets = _generate_buckets(start, end, granularity)
    bucket_expr = func.date_trunc(granularity, CallMetricsDaily.call_date)

    member_ctx = RlsContext.system(
        "user",
        user_id=str(current_user.id),
        role=UserRole.GROUP_ADMIN.value,
        group_id=str(current_user.group_id),
        institution_id=institution_id,
    )

    with use_rls_context(member_ctx):
        async with get_db_session() as session:
            locations = (
                await session.execute(
                    select(InstitutionLocation.id, InstitutionLocation.name, InstitutionLocation.slug)
                    .where(InstitutionLocation.institution_id == institution_id)
                    .order_by(InstitutionLocation.name)
                )
            ).all()
            location_ids = {str(loc.id) for loc in locations}
            selected = location_id if (location_id and location_id in location_ids) else None
            loc_filter = _rollup_location_filter(selected) if selected else None

            kpis = await _range_rollup_kpis(
                session,
                institution_id=institution_id,
                start=start,
                end_cap=end_cap,
                location_filter=loc_filter,
            )

            trend_filters = [
                CallMetricsDaily.institution_id == institution_id,
                CallMetricsDaily.call_date >= start,
                CallMetricsDaily.call_date < end_cap,
            ]
            if loc_filter is not None:
                trend_filters.append(loc_filter)
            trend_rows = (
                await session.execute(
                    select(
                        bucket_expr.label("bucket"),
                        func.coalesce(func.sum(CallMetricsDaily.total_calls), 0).label("total_calls"),
                        func.coalesce(func.sum(_appt_booked_from_jsonb()), 0).label("appointments"),
                        func.coalesce(func.sum(CallMetricsDaily.new_patient_calls), 0).label("new_patients"),
                    )
                    .where(*trend_filters)
                    .group_by(bucket_expr)
                )
            ).all()

            tag_rows = await _range_tag_distribution(
                session,
                institution_id=institution_id,
                start=start,
                end_cap=end_cap,
                include_today=False,
                today=today,
                location_id=selected,
                extra_conditions=[],
            )

            # Per-location comparison (only meaningful when not already scoped to one).
            loc_comparison: list[LocationComparisonRow] = []
            if selected is None:
                per_loc = (
                    await session.execute(
                        select(
                            CallMetricsDaily.location_id.label("location_id"),
                            func.coalesce(func.sum(CallMetricsDaily.total_calls), 0).label("total_calls"),
                            func.coalesce(func.sum(_appt_booked_from_jsonb()), 0).label("appointments"),
                            func.coalesce(func.sum(CallMetricsDaily.new_patient_calls), 0).label("new_patients"),
                            func.coalesce(func.sum(CallMetricsDaily.total_duration_seconds), 0).label("duration"),
                        )
                        .where(
                            CallMetricsDaily.institution_id == institution_id,
                            CallMetricsDaily.call_date >= start,
                            CallMetricsDaily.call_date < end_cap,
                            CallMetricsDaily.location_id != NULL_LOCATION_SENTINEL,
                        )
                        .group_by(CallMetricsDaily.location_id)
                    )
                ).all()
                name_by_id = {str(loc.id): loc.name for loc in locations}
                for row in per_loc:
                    lid = str(row.location_id)
                    calls = int(row.total_calls or 0)
                    bookings = int(row.appointments or 0)
                    loc_comparison.append(
                        LocationComparisonRow(
                            location_id=lid,
                            location_name=name_by_id.get(lid, "Unassigned"),
                            total_calls=calls,
                            appointments_booked=bookings,
                            new_patients=int(row.new_patients or 0),
                            booking_rate=_booking_rate(bookings, calls),
                            avg_call_duration_seconds=_avg_duration(int(row.duration or 0), calls),
                        )
                    )
                loc_comparison.sort(key=lambda r: r.total_calls, reverse=True)

    calls = int(kpis.total_calls or 0)
    bookings = int(kpis.appointments or 0)

    trend_totals = {b: {"total_calls": 0, "appointments": 0, "new_patients": 0} for b in buckets}
    for row in trend_rows:
        bucket = row.bucket.date() if hasattr(row.bucket, "date") else row.bucket
        slot = trend_totals.get(_bucket_start(bucket, granularity))
        if slot is not None:
            slot["total_calls"] += int(row.total_calls or 0)
            slot["appointments"] += int(row.appointments or 0)
            slot["new_patients"] += int(row.new_patients or 0)
    trend = [
        GroupTrendPoint(
            bucket=b.isoformat(),
            label=_bucket_label(b, granularity),
            total_calls=trend_totals[b]["total_calls"],
            appointments_booked=trend_totals[b]["appointments"],
            new_patients=trend_totals[b]["new_patients"],
        )
        for b in buckets
    ]

    log_audit_background(
        actor=AuditActor.ADMIN,
        user_id=str(current_user.id),
        action=AuditAction.VIEW_DASHBOARD,
        target_resource=f"group:institution:{institution_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "actor_role": current_user.role,
            "group_id": str(current_user.group_id),
            "location_id": selected,
            "range": f"{start.isoformat()}..{end.isoformat()}",
        },
        institution_id=institution_id,
    )

    return GroupInstitutionDashboardResponse(
        institution_id=institution_id,
        institution_name=institution_name,
        start_date=start,
        end_date=end,
        selected_location_id=selected,
        locations=[GroupLocationInfo(id=str(loc.id), name=loc.name, slug=loc.slug) for loc in locations],
        summary=InstitutionKpis(
            total_calls=calls,
            appointments_booked=bookings,
            new_patients=int(kpis.new_patients or 0),
            booking_rate=_booking_rate(bookings, calls),
            avg_call_duration_seconds=_avg_duration(int(kpis.duration or 0), calls),
        ),
        trend=trend,
        tag_distribution=tag_rows,
        location_comparison=loc_comparison,
        as_of=now.isoformat(),
    )
