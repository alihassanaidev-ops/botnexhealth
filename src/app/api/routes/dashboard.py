"""
Dashboard summary route — institution-facing API for call volume metrics and queues.

Provides aggregate call statistics and the needs-callback queue used on
the institution dashboard page.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import Integer, and_, case, func, select

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
from src.app.services.sms_privacy import mask_phone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/dashboard", tags=["Dashboard"])


# ── Response models ───────────────────────────────────────────────────────────


class CallVolume(BaseModel):
    today: int
    this_week: int
    this_month: int
    all_time: int


class RangeMetrics(BaseModel):
    """Stats scoped to a caller-selected date range (inclusive both ends).

    Computed from the ``call_metrics_daily`` rollup (historical days) plus a
    live overlay for today when the range includes it — same light-query
    pattern as the fixed-window cards, just bracketed by [start, end].
    """

    start_date: date
    end_date: date
    total_calls: int
    appointments_booked: int
    new_patients: int
    booking_rate: float
    avg_call_duration_seconds: float


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
    # Masked callback number; full value via POST /institution/calls/{id}/reveal/phone.
    phone_masked: str | None = None
    phone_reveal_available: bool = False


class DashboardSummary(BaseModel):
    call_volume: CallVolume
    tag_counts: list[TagCount]
    callback_queue: list[CallbackQueueItem]   # unresolved needs_callback calls, oldest first
    as_of: str                                 # ISO timestamp of when this was computed
    # KPI cards — month-to-date, scoped to whatever extra_conditions
    # apply (the user's pinned location for STAFF/LOCATION_ADMIN, the
    # selected location_slug for INSTITUTION_ADMIN, or institution-wide
    # when no slug is supplied). Surfaced here so non-institution-admin
    # users see real numbers instead of the previous frontend-hardcoded
    # zeroes when /dashboard/aggregate was the only KPI source.
    appointments_booked_month: int = 0
    new_patients_month: int = 0
    booking_rate_month: float = 0.0
    avg_call_duration_seconds: float = 0.0
    # Present only when the caller supplies start_date/end_date. When set, the
    # frontend drives the KPI cards + tag_counts from this range instead of the
    # fixed-window call_volume above.
    range: RangeMetrics | None = None


class MonthlyMetricPoint(BaseModel):
    month: str
    month_label: str
    total_calls_month: int
    appointments_booked_month: int
    new_patients_month: int
    booking_rate_month: float
    avg_call_duration_seconds: float


class DashboardMonthlyMetrics(BaseModel):
    points: list[MonthlyMetricPoint]
    as_of: str


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


def _add_months(month_start: date, months: int) -> date:
    month_index = month_start.month - 1 + months
    year = month_start.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


# ── Date-range window resolution ─────────────────────────────────────
#
# One shared helper so /summary, /monthly-metrics, and /aggregate agree on
# boundary semantics. The rollup half is bracketed [start, end_cap); the live
# "today" row is overlaid only when the range includes today (the rollup lags
# ~5 min and excludes today, exactly like the fixed-window code).

_MAX_RANGE_DAYS = 731  # ~2 years; presets top out at 90, but custom is open


def _resolve_window(
    start_date: date | None,
    end_date: date | None,
    today: date,
) -> tuple[date, date, date, bool]:
    """Resolve an inclusive [start, end] selection into query brackets.

    Returns ``(start, end, end_cap, include_today)`` where rollup rows are
    filtered ``call_date >= start AND call_date < end_cap`` and the live today
    row is added iff ``include_today``. Defaults to the last 30 days ending
    today when either bound is omitted.
    """
    if end_date is None:
        end_date = today
    if end_date > today:  # no data in the future; clamp
        end_date = today
    if start_date is None:
        start_date = end_date - timedelta(days=29)
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be on or before end_date",
        )
    if (end_date - start_date).days > _MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"date range may not exceed {_MAX_RANGE_DAYS} days",
        )
    include_today = end_date >= today
    end_cap = today if include_today else end_date + timedelta(days=1)
    return start_date, end_date, end_cap, include_today


_APPT_BOOKED = CallStatus.APPOINTMENT_BOOKED.value


def _appt_booked_from_jsonb():
    """``tag_counts ->> 'appointment_booked'`` cast to int, NULL-safe."""
    return func.coalesce(
        func.cast(CallMetricsDaily.tag_counts.op("->>")(_APPT_BOOKED), Integer),
        0,
    )


async def _range_rollup_kpis(session, *, institution_id, start, end_cap, location_filter):
    """Range KPI sums from the rollup — one light SUM over a few hundred rows."""
    filters = [
        CallMetricsDaily.institution_id == institution_id,
        CallMetricsDaily.call_date >= start,
        CallMetricsDaily.call_date < end_cap,
    ]
    if location_filter is not None:
        filters.append(location_filter)
    return (
        await session.execute(
            select(
                func.coalesce(func.sum(CallMetricsDaily.total_calls), 0).label("total_calls"),
                func.coalesce(func.sum(CallMetricsDaily.new_patient_calls), 0).label("new_patients"),
                func.coalesce(func.sum(_appt_booked_from_jsonb()), 0).label("appointments"),
                func.coalesce(func.sum(CallMetricsDaily.total_duration_seconds), 0).label("duration"),
            ).where(*filters)
        )
    ).one()


async def _today_live_kpis(session, *, institution_id, today, extra_conditions):
    """Today's live KPI components (count, appts, new patients, duration)."""
    return (
        await session.execute(
            select(
                _count_calls(Call.call_date == today).label("today_count"),
                func.coalesce(
                    func.sum(
                        case((Call.call_status == _APPT_BOOKED, 1), else_=0)
                    ),
                    0,
                ).label("today_appointments_booked"),
                func.coalesce(
                    func.sum(case((Call.is_new_patient.is_(True), 1), else_=0)),
                    0,
                ).label("today_new_patients"),
                func.coalesce(
                    func.sum(func.coalesce(Call.call_duration_seconds, 0)), 0
                ).label("today_duration"),
            ).where(
                Call.institution_id == institution_id,
                Call.call_date == today,
                *extra_conditions,
            )
        )
    ).one()


async def _range_tag_distribution(
    session,
    *,
    institution_id: str,
    start: date,
    end_cap: date,
    include_today: bool,
    today: date,
    location_id: str | None,
    extra_conditions,
) -> list["TagCount"]:
    """Tag distribution over [start, end] — rollup jsonb SUM + today live.

    Mirrors :func:`_load_aggregate_tag_distribution` but bracketed by an
    arbitrary range and optionally scoped to one location. Stays light by
    summing the rollup's JSONB ``tag_counts`` rather than scanning ``calls``.
    """
    from sqlalchemy import text

    loc_clause = "AND location_id = :location_id" if location_id is not None else ""
    rollup_rows = (
        await session.execute(
            text(
                f"""
                SELECT t.key AS tag, SUM(t.value::int) AS cnt
                FROM call_metrics_daily,
                     jsonb_each_text(call_metrics_daily.tag_counts) AS t(key, value)
                WHERE call_metrics_daily.institution_id = :institution_id
                  AND call_metrics_daily.call_date >= :start
                  AND call_metrics_daily.call_date < :end_cap
                  {loc_clause}
                GROUP BY t.key
                """
            ),
            {
                "institution_id": institution_id,
                "start": start,
                "end_cap": end_cap,
                "location_id": location_id,
            },
        )
    ).all()

    totals: dict[str, int] = {}
    for row in rollup_rows:
        if row.tag is None:
            continue
        totals[row.tag] = totals.get(row.tag, 0) + int(row.cnt or 0)

    if include_today:
        live_rows = (
            await session.execute(
                select(Call.call_status, func.count(Call.id).label("cnt"))
                .where(
                    Call.institution_id == institution_id,
                    Call.call_date == today,
                    Call.call_status.isnot(None),
                    *extra_conditions,
                )
                .group_by(Call.call_status)
            )
        ).all()
        for row in live_rows:
            if row.call_status is None:
                continue
            totals[row.call_status] = totals.get(row.call_status, 0) + int(row.cnt or 0)

    return [
        TagCount(
            tag=tag,
            label=TAG_LABELS.get(tag, tag.replace("_", " ").title()),
            count=count,
        )
        for tag, count in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]


# ── Adaptive trend granularity ───────────────────────────────────────


def _granularity_for_span(span_days: int) -> str:
    """Pick bucket size so a short range reads day-by-day, a long one coarser."""
    if span_days <= 31:
        return "day"
    if span_days <= 92:
        return "week"
    return "month"


def _bucket_start(d: date, granularity: str) -> date:
    if granularity == "day":
        return d
    if granularity == "week":
        return d - timedelta(days=d.weekday())  # Monday — matches date_trunc('week')
    return d.replace(day=1)


def _next_bucket(d: date, granularity: str) -> date:
    if granularity == "day":
        return d + timedelta(days=1)
    if granularity == "week":
        return d + timedelta(days=7)
    return _add_months(d, 1)


def _bucket_label(d: date, granularity: str) -> str:
    if granularity == "month":
        return d.strftime("%b %Y")
    return f"{d.strftime('%b')} {d.day}"


def _generate_buckets(start: date, end: date, granularity: str) -> list[date]:
    """Every bucket-start in [start, end], so the trend zero-fills empty days."""
    buckets: list[date] = []
    cursor = _bucket_start(start, granularity)
    end_bucket = _bucket_start(end, granularity)
    while cursor <= end_bucket:
        buckets.append(cursor)
        cursor = _next_bucket(cursor, granularity)
    return buckets


async def _resolve_dashboard_scope(
    session,
    *,
    current_user: User,
    institution_id: str,
    location_slug: str | None,
):
    extra_conditions = []
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

    return extra_conditions, rollup_location_id


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
                # KPI columns: same shape the aggregate endpoint computes,
                # restricted by ``rollup_filters`` so a location-scoped
                # caller gets numbers for their location only.
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CallMetricsDaily.call_date >= month_start,
                                CallMetricsDaily.new_patient_calls,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("new_patients_month"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CallMetricsDaily.call_date >= month_start,
                                func.coalesce(
                                    func.cast(
                                        CallMetricsDaily.tag_counts.op("->>")(
                                            CallStatus.APPOINTMENT_BOOKED.value
                                        ),
                                        Integer,
                                    ),
                                    0,
                                ),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("appointments_booked_month"),
                func.coalesce(
                    func.sum(CallMetricsDaily.total_duration_seconds), 0
                ).label("all_time_duration"),
            ).where(*rollup_filters)
        )
    ).one()


# ── Aggregate dashboard helpers ──────────────────────────────────────
#
# The aggregate dashboard endpoint uses these to keep its body short
# and to make the rollup-vs-live split explicit at the call site.
# Each helper issues exactly one query; the endpoint stitches their
# results together.

async def _load_aggregate_summary_from_rollup(
    session,
    *,
    institution_id: str,
    today,
    week_start,
    month_start,
):
    """Institution-wide aggregate metrics from ``call_metrics_daily``.

    Bracketed by ``call_date < today`` — today's contribution comes from
    :func:`_load_today_and_open_callbacks_live`. ``appointments_booked``
    is read from the JSONB ``tag_counts`` column rather than a dedicated
    column, so adding new ``CallStatus`` values doesn't require a
    schema migration; the cost is one ``->>`` lookup per row, which the
    planner handles cheaply since the rollup is tiny.
    """
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
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CallMetricsDaily.call_date >= month_start,
                                CallMetricsDaily.new_patient_calls,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("new_patients_month"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CallMetricsDaily.call_date >= month_start,
                                func.coalesce(
                                    func.cast(
                                        CallMetricsDaily.tag_counts.op("->>")(
                                            CallStatus.APPOINTMENT_BOOKED.value
                                        ),
                                        Integer,
                                    ),
                                    0,
                                ),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("appointments_booked_month"),
                func.coalesce(
                    func.sum(CallMetricsDaily.total_duration_seconds), 0
                ).label("all_time_duration"),
            ).where(
                CallMetricsDaily.institution_id == institution_id,
                CallMetricsDaily.call_date < today,
            )
        )
    ).one()


async def _load_today_and_open_callbacks_live(
    session,
    *,
    institution_id: str,
    today,
    month_start,  # noqa: ARG001 — kept for symmetric signature; predicate is on today only
):
    """Today's live counts + open-callbacks count.

    Three predicates collapsed into one query against ``calls`` so we
    pay the round-trip cost once. ``open_callbacks`` is unbracketed by
    date — it's the count of unresolved needs-callback rows regardless
    of how old they are, since callbacks can stay open across many
    days. The partial index ``ix_call_dashboard_open_callbacks`` makes
    this branch a single index-only scan.
    """
    return (
        await session.execute(
            select(
                _count_calls(Call.call_date == today).label("today_count"),
                _count_calls(
                    and_(
                        Call.call_date == today,
                        Call.call_status == CallStatus.APPOINTMENT_BOOKED.value,
                    )
                ).label("today_appointments_booked"),
                _count_calls(
                    and_(
                        Call.call_date == today,
                        Call.is_new_patient.is_(True),
                    )
                ).label("today_new_patients"),
                func.coalesce(
                    func.sum(
                        case(
                            (Call.call_date == today, Call.call_duration_seconds),
                            else_=0,
                        )
                    ),
                    0,
                ).label("today_duration"),
                _count_calls(
                    and_(
                        Call.call_status == CallStatus.NEEDS_CALLBACK.value,
                        Call.callback_resolved.is_(False),
                    )
                ).label("open_callbacks"),
            ).where(Call.institution_id == institution_id)
        )
    ).one()


async def _load_aggregate_tag_distribution(
    session,
    *,
    institution_id: str,
    today,
    month_start,
) -> list["TagCount"]:
    """Tag distribution scoped to month-to-date (rollup-historical + today-live).

    Window matches the KPI cards (``appointments_booked_month`` etc.) so
    that the tag-breakdown chart and the headline KPIs always tell a
    consistent story. Without this scoping the breakdown was all-time
    while KPIs were month-only, which made e.g. 4 historical
    "appointment_booked" tags coexist with a 0 "Appointments Booked"
    KPI in the same view.

    Two small queries: a SUM-by-key over the rollup's JSONB column for
    every day in [month_start, today), plus a live
    ``GROUP BY call_status`` for today. Merged in Python and re-sorted
    descending. SQLAlchemy's table-valued helpers don't compose with
    ``func.sum`` cleanly here; the lateral cross join with
    ``jsonb_each_text`` reads naturally as raw SQL and stays one
    statement.
    """
    from sqlalchemy import text

    rollup_rows = (
        await session.execute(
            text(
                """
                SELECT t.key AS tag, SUM(t.value::int) AS cnt
                FROM call_metrics_daily,
                     jsonb_each_text(call_metrics_daily.tag_counts) AS t(key, value)
                WHERE call_metrics_daily.institution_id = :institution_id
                  AND call_metrics_daily.call_date < :today
                  AND call_metrics_daily.call_date >= :month_start
                GROUP BY t.key
                """
            ),
            {
                "institution_id": institution_id,
                "today": today,
                "month_start": month_start,
            },
        )
    ).all()

    live_rows = (
        await session.execute(
            select(Call.call_status, func.count(Call.id).label("cnt"))
            .where(
                Call.institution_id == institution_id,
                Call.call_date == today,
                Call.call_status.isnot(None),
            )
            .group_by(Call.call_status)
        )
    ).all()

    totals: dict[str, int] = {}
    for row in rollup_rows:
        if row.tag is None:
            continue
        totals[row.tag] = totals.get(row.tag, 0) + int(row.cnt or 0)
    for row in live_rows:
        if row.call_status is None:
            continue
        totals[row.call_status] = totals.get(row.call_status, 0) + int(row.cnt or 0)

    return [
        TagCount(
            tag=tag,
            label=TAG_LABELS.get(tag, tag.replace("_", " ").title()),
            count=count,
        )
        for tag, count in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]


async def _range_trend_metrics(
    *,
    current_user: User,
    institution_id: str,
    location_slug: str | None,
    start_date: date | None,
    end_date: date | None,
    now: datetime,
    today: date,
) -> DashboardMonthlyMetrics:
    """Adaptive-granularity trend over an explicit [start, end] range.

    Buckets are pre-generated and zero-filled so a short, sparse range still
    renders a continuous x-axis. All aggregation is a single GROUP BY over the
    daily rollup; today (when included) is overlaid from one live query.
    """
    r_start, r_end, r_end_cap, r_include_today = _resolve_window(start_date, end_date, today)
    span_days = (r_end - r_start).days + 1
    granularity = _granularity_for_span(span_days)
    buckets = _generate_buckets(r_start, r_end, granularity)

    totals: dict[date, dict[str, int]] = {
        bucket: {"total_calls": 0, "new_patients": 0, "appointments": 0, "duration": 0}
        for bucket in buckets
    }

    async with get_db_session() as session:
        extra_conditions, rollup_location_id = await _resolve_dashboard_scope(
            session,
            current_user=current_user,
            institution_id=institution_id,
            location_slug=location_slug,
        )

        bucket_expr = func.date_trunc(granularity, CallMetricsDaily.call_date)
        rollup_filters = [
            CallMetricsDaily.institution_id == institution_id,
            CallMetricsDaily.call_date >= r_start,
            CallMetricsDaily.call_date < r_end_cap,
        ]
        location_filter = _rollup_location_filter(rollup_location_id)
        if location_filter is not None:
            rollup_filters.append(location_filter)

        rollup_rows = (
            await session.execute(
                select(
                    bucket_expr.label("bucket"),
                    func.coalesce(func.sum(CallMetricsDaily.total_calls), 0).label("total_calls"),
                    func.coalesce(func.sum(CallMetricsDaily.new_patient_calls), 0).label("new_patients"),
                    func.coalesce(func.sum(_appt_booked_from_jsonb()), 0).label("appointments"),
                    func.coalesce(func.sum(CallMetricsDaily.total_duration_seconds), 0).label("duration"),
                )
                .where(*rollup_filters)
                .group_by(bucket_expr)
                .order_by(bucket_expr)
            )
        ).all()

        for row in rollup_rows:
            bucket = row.bucket.date() if hasattr(row.bucket, "date") else row.bucket
            if bucket not in totals:
                continue
            totals[bucket]["total_calls"] += int(row.total_calls or 0)
            totals[bucket]["new_patients"] += int(row.new_patients or 0)
            totals[bucket]["appointments"] += int(row.appointments or 0)
            totals[bucket]["duration"] += int(row.duration or 0)

        if r_include_today:
            live_row = await _today_live_kpis(
                session,
                institution_id=institution_id,
                today=today,
                extra_conditions=extra_conditions,
            )
            today_bucket = _bucket_start(today, granularity)
            if today_bucket in totals:
                totals[today_bucket]["total_calls"] += int(live_row.today_count or 0)
                totals[today_bucket]["new_patients"] += int(live_row.today_new_patients or 0)
                totals[today_bucket]["appointments"] += int(live_row.today_appointments_booked or 0)
                totals[today_bucket]["duration"] += int(live_row.today_duration or 0)

    points = []
    for bucket in buckets:
        item = totals[bucket]
        total_calls = item["total_calls"]
        appointments = item["appointments"]
        booking_rate = round((appointments / total_calls) * 100, 2) if total_calls else 0.0
        avg_duration = round(item["duration"] / total_calls, 2) if total_calls else 0.0
        points.append(
            MonthlyMetricPoint(
                month=bucket.isoformat(),
                month_label=_bucket_label(bucket, granularity),
                total_calls_month=total_calls,
                appointments_booked_month=appointments,
                new_patients_month=item["new_patients"],
                booking_rate_month=booking_rate,
                avg_call_duration_seconds=avg_duration,
            )
        )

    return DashboardMonthlyMetrics(points=points, as_of=now.isoformat())


@router.get("/monthly-metrics", response_model=DashboardMonthlyMetrics)
@limiter.limit(RATE_READ)
async def get_dashboard_monthly_metrics(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_slug: str | None = Query(None),
    months: int = Query(6, ge=1, le=12),
    start_date: date | None = Query(None, description="Inclusive range start (YYYY-MM-DD)"),
    end_date: date | None = Query(None, description="Inclusive range end (YYYY-MM-DD)"),
) -> DashboardMonthlyMetrics:
    """Return dashboard trend metrics for charting.

    Default: month-by-month for the last ``months`` months. When
    ``start_date``/``end_date`` are supplied, the trend spans that range with
    adaptive granularity — daily (<=31d), weekly (<=92d), or monthly — every
    bucket zero-filled so short, sparse ranges still chart cleanly.
    """
    if not current_user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not associated with an institution",
        )

    institution_id = current_user.institution_id
    now = datetime.now(timezone.utc)
    today = now.date()

    if start_date is not None or end_date is not None:
        return await _range_trend_metrics(
            current_user=current_user,
            institution_id=institution_id,
            location_slug=location_slug,
            start_date=start_date,
            end_date=end_date,
            now=now,
            today=today,
        )

    current_month = today.replace(day=1)
    first_month = _add_months(current_month, -(months - 1))
    month_starts = [_add_months(first_month, offset) for offset in range(months)]
    appointment_status = CallStatus.APPOINTMENT_BOOKED.value

    async with get_db_session() as session:
        extra_conditions, rollup_location_id = await _resolve_dashboard_scope(
            session,
            current_user=current_user,
            institution_id=institution_id,
            location_slug=location_slug,
        )

        rollup_filters = [
            CallMetricsDaily.institution_id == institution_id,
            CallMetricsDaily.call_date >= first_month,
            CallMetricsDaily.call_date < today,
        ]
        location_filter = _rollup_location_filter(rollup_location_id)
        if location_filter is not None:
            rollup_filters.append(location_filter)

        month_expr = func.date_trunc("month", CallMetricsDaily.call_date)
        appointment_count_expr = func.coalesce(
            func.cast(
                CallMetricsDaily.tag_counts.op("->>")(appointment_status),
                Integer,
            ),
            0,
        )
        rollup_rows = (
            await session.execute(
                select(
                    month_expr.label("month_start"),
                    func.coalesce(func.sum(CallMetricsDaily.total_calls), 0).label("total_calls"),
                    func.coalesce(func.sum(CallMetricsDaily.new_patient_calls), 0).label("new_patients"),
                    func.coalesce(func.sum(appointment_count_expr), 0).label("appointments"),
                    func.coalesce(func.sum(CallMetricsDaily.total_duration_seconds), 0).label("duration"),
                )
                .where(*rollup_filters)
                .group_by(month_expr)
                .order_by(month_expr)
            )
        ).all()

        live_row = (
            await session.execute(
                select(
                    func.count(Call.id).label("total_calls"),
                    func.coalesce(
                        func.sum(case((Call.is_new_patient.is_(True), 1), else_=0)),
                        0,
                    ).label("new_patients"),
                    func.coalesce(
                        func.sum(
                            case(
                                (Call.call_status == appointment_status, 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("appointments"),
                    func.coalesce(func.sum(func.coalesce(Call.call_duration_seconds, 0)), 0).label("duration"),
                ).where(
                    Call.institution_id == institution_id,
                    Call.call_date == today,
                    *extra_conditions,
                )
            )
        ).one()

    totals: dict[date, dict[str, int]] = {
        month_start: {
            "total_calls": 0,
            "new_patients": 0,
            "appointments": 0,
            "duration": 0,
        }
        for month_start in month_starts
    }

    for row in rollup_rows:
        row_month = row.month_start.date() if hasattr(row.month_start, "date") else row.month_start
        row_month = row_month.replace(day=1)
        if row_month not in totals:
            continue
        totals[row_month]["total_calls"] += int(row.total_calls or 0)
        totals[row_month]["new_patients"] += int(row.new_patients or 0)
        totals[row_month]["appointments"] += int(row.appointments or 0)
        totals[row_month]["duration"] += int(row.duration or 0)

    totals[current_month]["total_calls"] += int(live_row.total_calls or 0)
    totals[current_month]["new_patients"] += int(live_row.new_patients or 0)
    totals[current_month]["appointments"] += int(live_row.appointments or 0)
    totals[current_month]["duration"] += int(live_row.duration or 0)

    points = []
    for month_start in month_starts:
        item = totals[month_start]
        total_calls = item["total_calls"]
        appointments = item["appointments"]
        booking_rate = round((appointments / total_calls) * 100, 2) if total_calls else 0.0
        avg_duration = round(item["duration"] / total_calls, 2) if total_calls else 0.0
        points.append(
            MonthlyMetricPoint(
                month=month_start.isoformat(),
                month_label=month_start.strftime("%b %Y"),
                total_calls_month=total_calls,
                appointments_booked_month=appointments,
                new_patients_month=item["new_patients"],
                booking_rate_month=booking_rate,
                avg_call_duration_seconds=avg_duration,
            )
        )

    return DashboardMonthlyMetrics(points=points, as_of=now.isoformat())


async def _load_per_location_metrics_from_rollup(
    session,
    *,
    institution_id: str,
    today,
    month_start,
):
    """Per-location aggregates from the rollup, indexed by location_id.

    The single query that replaces the legacy ``GROUP BY
    Call.location_id`` over the entire ``calls`` table. The rollup PK is
    ``(institution_id, location_id, call_date)`` so this is an index
    scan over a tiny number of rows (<= one row per location-day in the
    history window).
    """
    rows = (
        await session.execute(
            select(
                CallMetricsDaily.location_id.label("location_id"),
                func.coalesce(func.sum(CallMetricsDaily.total_calls), 0).label("total_calls"),
                func.coalesce(
                    func.sum(
                        case(
                            (CallMetricsDaily.call_date >= month_start, CallMetricsDaily.total_calls),
                            else_=0,
                        )
                    ),
                    0,
                ).label("calls_this_month"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CallMetricsDaily.call_date >= month_start,
                                CallMetricsDaily.new_patient_calls,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("new_patients_month"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CallMetricsDaily.call_date >= month_start,
                                func.coalesce(
                                    func.cast(
                                        CallMetricsDaily.tag_counts.op("->>")(
                                            CallStatus.APPOINTMENT_BOOKED.value
                                        ),
                                        Integer,
                                    ),
                                    0,
                                ),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("appointments_booked_month"),
                func.coalesce(
                    func.sum(CallMetricsDaily.total_duration_seconds), 0
                ).label("total_duration_seconds"),
            )
            .where(
                CallMetricsDaily.institution_id == institution_id,
                CallMetricsDaily.call_date < today,
            )
            .group_by(CallMetricsDaily.location_id)
        )
    ).all()
    return {row.location_id: row for row in rows}


async def _load_per_location_today_and_open_live(
    session,
    *,
    institution_id: str,
    today,
):
    """Today + open_callbacks per location, in one live GROUP BY.

    Open callbacks are unbracketed by date (an unresolved row from
    six months ago still belongs in the queue). The partial index
    ``ix_call_dashboard_open_callbacks`` keeps this fast.
    """
    rows = (
        await session.execute(
            select(
                Call.location_id.label("location_id"),
                _count_calls(Call.call_date == today).label("calls_today"),
                _count_calls(
                    and_(
                        Call.call_date == today,
                        Call.call_status == CallStatus.APPOINTMENT_BOOKED.value,
                    )
                ).label("today_appointments_booked"),
                _count_calls(
                    and_(
                        Call.call_date == today,
                        Call.is_new_patient.is_(True),
                    )
                ).label("today_new_patients"),
                func.coalesce(
                    func.sum(
                        case(
                            (Call.call_date == today, Call.call_duration_seconds),
                            else_=0,
                        )
                    ),
                    0,
                ).label("today_duration"),
                _count_calls(
                    and_(
                        Call.call_status == CallStatus.NEEDS_CALLBACK.value,
                        Call.callback_resolved.is_(False),
                    )
                ).label("open_callbacks"),
            )
            .where(Call.institution_id == institution_id)
            .group_by(Call.location_id)
        )
    ).all()
    return {row.location_id: row for row in rows}


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=DashboardSummary)
@limiter.limit(RATE_READ)
async def get_dashboard_summary(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_slug: str | None = Query(None),
    start_date: date | None = Query(None, description="Inclusive range start (YYYY-MM-DD)"),
    end_date: date | None = Query(None, description="Inclusive range end (YYYY-MM-DD)"),
) -> DashboardSummary:
    """
    Return call volume metrics, per-tag counts, and the unresolved callback queue
    for the authenticated institution.

    When ``start_date``/``end_date`` are supplied, a ``range`` block is added with
    KPIs scoped to that window and ``tag_counts`` is re-scoped to the same range
    (both computed from the daily rollup so the query stays light). Without them,
    the response is unchanged (fixed today/week/month/all-time windows).
    """
    range_requested = start_date is not None or end_date is not None
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

        # Today's live KPI components — used to overlay onto the rollup
        # for the month-to-date numbers (see KPI math after the response
        # is built). One round-trip; predicates are tight index ranges
        # under the (institution_id, call_date) covering index.
        today_kpi_row = (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Call.call_status == CallStatus.APPOINTMENT_BOOKED.value,
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("today_appointments_booked"),
                    func.coalesce(
                        func.sum(case((Call.is_new_patient.is_(True), 1), else_=0)),
                        0,
                    ).label("today_new_patients"),
                    func.coalesce(
                        func.sum(func.coalesce(Call.call_duration_seconds, 0)), 0
                    ).label("today_duration"),
                ).where(
                    Call.institution_id == institution_id,
                    Call.call_date == today,
                    *extra_conditions,
                )
            )
        ).one()

        # ── Tag counts (by primary call_status) ───────────────────────────
        # Scoped to month-to-date so the breakdown agrees with the KPI
        # cards (also month-to-date). Mixing all-time tags with
        # month-only KPIs in the same view produced inconsistent numbers
        # — e.g. 4 historical "appointment_booked" tags next to a 0
        # "Appointments Booked" KPI.
        tag_rows = (
            await session.execute(
                select(Call.call_status, func.count(Call.id).label("cnt"))
                .where(
                    Call.institution_id == institution_id,
                    Call.call_status.isnot(None),
                    Call.call_date >= month_start,
                    *extra_conditions,
                )
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
                phone_masked=(
                    mask_phone(contact.phone)
                    if contact and contact.phone_encrypted is not None
                    else None
                ),
                phone_reveal_available=bool(contact and contact.phone_encrypted is not None),
            )
            for call, contact in callback_rows
        ]

        # Stitch rollup totals (date < today) with the live ``today_count``.
        # Today is bracketed in [week_start, today] and [month_start, today]
        # so it must always be added to the bucketed sums.
        week_total = int(rollup_row.week_total or 0) + today_count
        month_total = int(rollup_row.month_total or 0) + today_count
        all_time_total = int(rollup_row.all_time_total or 0) + today_count

        # ── KPI cards ─────────────────────────────────────────────────────
        # Same rollup + today-live overlay pattern the aggregate endpoint
        # uses, scoped to the same extra_conditions as the rest of this
        # response so location-pinned roles see numbers for their location.
        appointments_booked_month = int(rollup_row.appointments_booked_month or 0) + int(
            today_kpi_row.today_appointments_booked or 0
        )
        new_patients_month = int(rollup_row.new_patients_month or 0) + int(
            today_kpi_row.today_new_patients or 0
        )
        booking_rate_month = (
            round((appointments_booked_month / month_total) * 100, 2)
            if month_total
            else 0.0
        )
        avg_call_duration_seconds = (
            round(
                (
                    int(rollup_row.all_time_duration or 0)
                    + int(today_kpi_row.today_duration or 0)
                )
                / all_time_total,
                2,
            )
            if all_time_total
            else 0.0
        )

        # ── Date-range KPIs (optional) ────────────────────────────────────
        # When the caller supplies a window, compute range-scoped KPIs and
        # re-scope tag_counts to match — both off the daily rollup so the
        # query stays light regardless of how wide the range is.
        range_metrics: RangeMetrics | None = None
        if range_requested:
            r_start, r_end, r_end_cap, r_include_today = _resolve_window(
                start_date, end_date, today
            )
            range_location_filter = _rollup_location_filter(rollup_location_id)
            range_row = await _range_rollup_kpis(
                session,
                institution_id=institution_id,
                start=r_start,
                end_cap=r_end_cap,
                location_filter=range_location_filter,
            )
            r_calls = int(range_row.total_calls or 0)
            r_appts = int(range_row.appointments or 0)
            r_new = int(range_row.new_patients or 0)
            r_duration = int(range_row.duration or 0)
            if r_include_today:
                r_today = await _today_live_kpis(
                    session,
                    institution_id=institution_id,
                    today=today,
                    extra_conditions=extra_conditions,
                )
                r_calls += int(r_today.today_count or 0)
                r_appts += int(r_today.today_appointments_booked or 0)
                r_new += int(r_today.today_new_patients or 0)
                r_duration += int(r_today.today_duration or 0)
            range_metrics = RangeMetrics(
                start_date=r_start,
                end_date=r_end,
                total_calls=r_calls,
                appointments_booked=r_appts,
                new_patients=r_new,
                booking_rate=round((r_appts / r_calls) * 100, 2) if r_calls else 0.0,
                avg_call_duration_seconds=round(r_duration / r_calls, 2) if r_calls else 0.0,
            )
            tag_counts = await _range_tag_distribution(
                session,
                institution_id=institution_id,
                start=r_start,
                end_cap=r_end_cap,
                include_today=r_include_today,
                today=today,
                location_id=rollup_location_id,
                extra_conditions=extra_conditions,
            )

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
            appointments_booked_month=appointments_booked_month,
            new_patients_month=new_patients_month,
            booking_rate_month=booking_rate_month,
            avg_call_duration_seconds=avg_call_duration_seconds,
            range=range_metrics,
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


async def _load_per_location_range_from_rollup(session, *, institution_id, start, end_cap):
    """Per-location range aggregates from the rollup, indexed by location_id."""
    rows = (
        await session.execute(
            select(
                CallMetricsDaily.location_id.label("location_id"),
                func.coalesce(func.sum(CallMetricsDaily.total_calls), 0).label("total_calls"),
                func.coalesce(func.sum(CallMetricsDaily.new_patient_calls), 0).label("new_patients"),
                func.coalesce(func.sum(_appt_booked_from_jsonb()), 0).label("appointments"),
                func.coalesce(func.sum(CallMetricsDaily.total_duration_seconds), 0).label("duration"),
            )
            .where(
                CallMetricsDaily.institution_id == institution_id,
                CallMetricsDaily.call_date >= start,
                CallMetricsDaily.call_date < end_cap,
            )
            .group_by(CallMetricsDaily.location_id)
        )
    ).all()
    return {row.location_id: row for row in rows}


async def _aggregate_range_response(
    *,
    institution_id: str,
    start_date: date | None,
    end_date: date | None,
    now: datetime,
    today: date,
) -> AggregateDashboardResponse:
    """Aggregate dashboard scoped to an explicit [start, end] range.

    Range-scopes the summary cards, tag distribution, and per-location
    comparison. ``calls_today`` / ``open_callbacks`` remain live (they are
    point-in-time, not range, concepts). All range aggregation is rollup SUMs;
    today is overlaid once when the range includes it.
    """
    r_start, r_end, r_end_cap, r_include_today = _resolve_window(start_date, end_date, today)

    async with get_db_session() as session:
        locations = (
            await session.execute(
                select(InstitutionLocation).where(InstitutionLocation.institution_id == institution_id)
            )
        ).scalars().all()

        range_row = await _range_rollup_kpis(
            session,
            institution_id=institution_id,
            start=r_start,
            end_cap=r_end_cap,
            location_filter=None,
        )
        calls = int(range_row.total_calls or 0)
        appts = int(range_row.appointments or 0)
        new_patients = int(range_row.new_patients or 0)
        duration = int(range_row.duration or 0)

        live_summary = await _load_today_and_open_callbacks_live(
            session,
            institution_id=institution_id,
            today=today,
            month_start=r_start,
        )
        total_calls_today = int(live_summary.today_count or 0)
        open_callbacks = int(live_summary.open_callbacks or 0)
        if r_include_today:
            calls += total_calls_today
            appts += int(live_summary.today_appointments_booked or 0)
            new_patients += int(live_summary.today_new_patients or 0)
            duration += int(live_summary.today_duration or 0)

        booking_rate = round((appts / calls) * 100, 2) if calls else 0.0
        avg_duration = round(duration / calls, 2) if calls else 0.0

        tag_distribution = await _range_tag_distribution(
            session,
            institution_id=institution_id,
            start=r_start,
            end_cap=r_end_cap,
            include_today=r_include_today,
            today=today,
            location_id=None,
            extra_conditions=[],
        )

        per_location_rollup = await _load_per_location_range_from_rollup(
            session,
            institution_id=institution_id,
            start=r_start,
            end_cap=r_end_cap,
        )
        per_location_live = await _load_per_location_today_and_open_live(
            session,
            institution_id=institution_id,
            today=today,
        )

        clinic_comparison: list[LocationComparisonRow] = []
        for loc in locations:
            rollup_row = per_location_rollup.get(loc.id)
            live_row = per_location_live.get(loc.id)
            calls_today_loc = int(live_row.calls_today) if live_row else 0
            loc_calls = (int(rollup_row.total_calls or 0) if rollup_row else 0) + (
                calls_today_loc if r_include_today else 0
            )
            loc_appts = (int(rollup_row.appointments or 0) if rollup_row else 0) + (
                (int(live_row.today_appointments_booked) if live_row else 0) if r_include_today else 0
            )
            loc_new = (int(rollup_row.new_patients or 0) if rollup_row else 0) + (
                (int(live_row.today_new_patients) if live_row else 0) if r_include_today else 0
            )
            loc_duration = (int(rollup_row.duration or 0) if rollup_row else 0) + (
                (int(live_row.today_duration or 0) if live_row else 0) if r_include_today else 0
            )
            clinic_comparison.append(
                LocationComparisonRow(
                    location_id=str(loc.id),
                    location_name=loc.name,
                    location_slug=loc.slug,
                    status="Active" if loc.is_active else "Inactive",
                    calls_today=calls_today_loc,
                    calls_this_month=loc_calls,
                    appointments_booked_month=loc_appts,
                    new_patients_month=loc_new,
                    booking_rate_month=round((loc_appts / loc_calls) * 100, 2) if loc_calls else 0.0,
                    avg_call_duration_seconds=round(loc_duration / loc_calls, 2) if loc_calls else 0.0,
                    open_callbacks=int(live_row.open_callbacks) if live_row else 0,
                )
            )

        clinic_comparison.sort(key=lambda row: row.calls_this_month, reverse=True)

        return AggregateDashboardResponse(
            summary=AggregateSummaryCards(
                total_calls_today=total_calls_today,
                total_calls_week=calls,
                total_calls_month=calls,
                total_calls_all_time=calls,
                appointments_booked_month=appts,
                new_patients_month=new_patients,
                booking_rate_month=booking_rate,
                avg_call_duration_seconds=avg_duration,
                open_callbacks=open_callbacks,
            ),
            tag_distribution=tag_distribution,
            clinic_comparison=clinic_comparison,
            as_of=now.isoformat(),
        )


@router.get("/aggregate", response_model=AggregateDashboardResponse)
@limiter.limit(RATE_READ)
async def get_aggregate_dashboard(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
    start_date: date | None = Query(None, description="Inclusive range start (YYYY-MM-DD)"),
    end_date: date | None = Query(None, description="Inclusive range end (YYYY-MM-DD)"),
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

    if start_date is not None or end_date is not None:
        response = await _aggregate_range_response(
            institution_id=institution_id,
            start_date=start_date,
            end_date=end_date,
            now=now,
            today=today,
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
                "location_count": len(response.clinic_comparison),
                "range": True,
            },
            institution_id=institution_id,
        )
        return response

    week_start = date.fromisocalendar(today.year, today.isocalendar().week, 1)
    month_start = today.replace(day=1)

    async with get_db_session() as session:
        locations = (
            await session.execute(
                select(InstitutionLocation).where(InstitutionLocation.institution_id == institution_id)
            )
        ).scalars().all()

        # ── Institution-wide summary ────────────────────────────────────
        # Rollup covers everything BEFORE today; today + open_callbacks
        # come from a single live query against ``calls``. Mirrors the
        # split used by ``get_dashboard_summary`` so reasoning about
        # staleness is identical across both endpoints.
        rollup_summary = await _load_aggregate_summary_from_rollup(
            session,
            institution_id=institution_id,
            today=today,
            week_start=week_start,
            month_start=month_start,
        )
        live_summary = await _load_today_and_open_callbacks_live(
            session,
            institution_id=institution_id,
            today=today,
            month_start=month_start,
        )

        total_calls_today = int(live_summary.today_count or 0)
        total_calls_week = int(rollup_summary.week_total or 0) + total_calls_today
        total_calls_month = int(rollup_summary.month_total or 0) + total_calls_today
        total_calls_all_time = int(rollup_summary.all_time_total or 0) + total_calls_today
        appointments_booked_month = (
            int(rollup_summary.appointments_booked_month or 0)
            + int(live_summary.today_appointments_booked or 0)
        )
        new_patients_month = (
            int(rollup_summary.new_patients_month or 0)
            + int(live_summary.today_new_patients or 0)
        )
        open_callbacks = int(live_summary.open_callbacks or 0)
        booking_rate_month = (
            round((appointments_booked_month / total_calls_month) * 100, 2)
            if total_calls_month
            else 0.0
        )

        # AVG = SUM / COUNT, computed across rollup-historical + today-live.
        # Avoids the precision drift of averaging two AVGs.
        total_duration_seconds = (
            int(rollup_summary.all_time_duration or 0)
            + int(live_summary.today_duration or 0)
        )
        avg_call_duration_seconds = (
            total_duration_seconds / total_calls_all_time
            if total_calls_all_time
            else 0.0
        )

        # ── Institution-wide tag distribution ───────────────────────────
        # Rollup historical tag_counts (jsonb) + today's live status
        # group-by, merged in Python. Both queries are tiny: rollup is
        # O(days × locations); the live query is bounded by today's call
        # count (<=hundreds for any one institution).
        tag_distribution = await _load_aggregate_tag_distribution(
            session,
            institution_id=institution_id,
            today=today,
            month_start=month_start,
        )

        # ── Per-location comparison ─────────────────────────────────────
        # The slowest single query in the legacy code: a full GROUP BY
        # on ``calls.location_id`` over the entire institution. Now:
        # rollup GROUP BY (small) + live today GROUP BY (small) + live
        # open_callbacks GROUP BY (partial-indexed). All three scale
        # with location count, not with calls volume.
        per_location_rollup = await _load_per_location_metrics_from_rollup(
            session,
            institution_id=institution_id,
            today=today,
            month_start=month_start,
        )
        per_location_live = await _load_per_location_today_and_open_live(
            session,
            institution_id=institution_id,
            today=today,
        )

        clinic_comparison: list[LocationComparisonRow] = []
        for loc in locations:
            rollup_row = per_location_rollup.get(loc.id)
            live_row = per_location_live.get(loc.id)
            calls_today_loc = int(live_row.calls_today) if live_row else 0
            calls_month_loc = (
                (int(rollup_row.calls_this_month or 0) if rollup_row else 0)
                + calls_today_loc
            )
            bookings_month_loc = (
                (int(rollup_row.appointments_booked_month or 0) if rollup_row else 0)
                + (int(live_row.today_appointments_booked) if live_row else 0)
            )
            duration_total = (
                (int(rollup_row.total_duration_seconds or 0) if rollup_row else 0)
                + (int(live_row.today_duration or 0) if live_row else 0)
            )
            calls_total_for_avg = (
                (int(rollup_row.total_calls or 0) if rollup_row else 0)
                + calls_today_loc
            )
            avg_duration = (
                duration_total / calls_total_for_avg if calls_total_for_avg else 0.0
            )
            clinic_comparison.append(
                LocationComparisonRow(
                    location_id=str(loc.id),
                    location_name=loc.name,
                    location_slug=loc.slug,
                    status="Active" if loc.is_active else "Inactive",
                    calls_today=calls_today_loc,
                    calls_this_month=calls_month_loc,
                    appointments_booked_month=bookings_month_loc,
                    new_patients_month=(
                        (int(rollup_row.new_patients_month or 0) if rollup_row else 0)
                        + (int(live_row.today_new_patients) if live_row else 0)
                    ),
                    booking_rate_month=(
                        round((bookings_month_loc / calls_month_loc) * 100, 2)
                        if calls_month_loc
                        else 0.0
                    ),
                    avg_call_duration_seconds=round(avg_duration, 2),
                    open_callbacks=int(live_row.open_callbacks) if live_row else 0,
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
