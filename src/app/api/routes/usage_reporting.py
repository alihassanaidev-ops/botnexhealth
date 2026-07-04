"""Usage & cost reporting API (Plan 11 M-2).

Institution-facing read endpoints over the ``usage_cost_rollups`` rollup (fast,
pre-aggregated) for per-channel spend, plus a per-campaign breakdown queried from
raw ``usage_events`` (admin-frequency). RLS scopes every query to the caller's
institution; results are additionally filtered by institution_id (belt + braces),
mirroring the dashboard routes.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select

from src.app.api.deps import get_current_active_user
from src.app.api.rate_limit import RATE_READ, limiter
from src.app.database import get_db_session
from src.app.models.institution_location import InstitutionLocation
from src.app.models.usage_cost_rollup import UsageCostRollup
from src.app.models.usage_event import UsageEvent
from src.app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/usage", tags=["Usage & Cost"])

_MAX_RANGE_DAYS = 731


# ── Response models ──────────────────────────────────────────────────────────


class ChannelUsage(BaseModel):
    channel: str
    event_count: int
    total_segments: int
    total_dials: int
    total_emails: int
    total_minutes: float
    total_cost: float


class UsageSummary(BaseModel):
    start_date: date
    end_date: date
    currency: str
    total_cost: float
    channels: list[ChannelUsage]


class CampaignUsage(BaseModel):
    workflow_id: str
    event_count: int
    total_cost: float
    total_segments: int
    total_minutes: float
    total_emails: int


class CampaignUsageReport(BaseModel):
    start_date: date
    end_date: date
    campaigns: list[CampaignUsage]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_window(start_date: date | None, end_date: date | None, today: date) -> tuple[date, date]:
    if end_date is None:
        end_date = today
    if end_date > today:
        end_date = today
    if start_date is None:
        start_date = end_date - timedelta(days=29)
    if start_date > end_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "start_date must be on or before end_date")
    if (end_date - start_date).days > _MAX_RANGE_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"date range may not exceed {_MAX_RANGE_DAYS} days")
    return start_date, end_date


async def _resolve_location_id(session, *, institution_id: str, location_slug: str | None) -> str | None:
    if not location_slug:
        return None
    result = await session.execute(
        select(InstitutionLocation.id).where(
            InstitutionLocation.institution_id == institution_id,
            InstitutionLocation.slug == location_slug,
        )
    )
    location_id = result.scalar_one_or_none()
    if location_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "location not found")
    return location_id


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=UsageSummary)
@limiter.limit(RATE_READ)
async def get_usage_summary(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    location_slug: str | None = Query(None),
    start_date: date | None = Query(None, description="Inclusive range start (YYYY-MM-DD)"),
    end_date: date | None = Query(None, description="Inclusive range end (YYYY-MM-DD)"),
) -> UsageSummary:
    """Per-channel usage + cost totals over a date range, from the daily rollup."""
    if not current_user.institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User is not associated with an institution")

    institution_id = current_user.institution_id
    today = date.today()
    start, end = _resolve_window(start_date, end_date, today)

    async with get_db_session() as session:
        location_id = await _resolve_location_id(
            session, institution_id=institution_id, location_slug=location_slug
        )
        filters = [
            UsageCostRollup.institution_id == institution_id,
            UsageCostRollup.usage_date >= start,
            UsageCostRollup.usage_date <= end,
        ]
        if location_id is not None:
            filters.append(UsageCostRollup.location_id == location_id)

        result = await session.execute(
            select(
                UsageCostRollup.channel,
                func.coalesce(func.sum(UsageCostRollup.event_count), 0).label("event_count"),
                func.coalesce(func.sum(UsageCostRollup.total_segments), 0).label("segments"),
                func.coalesce(func.sum(UsageCostRollup.total_dials), 0).label("dials"),
                func.coalesce(func.sum(UsageCostRollup.total_emails), 0).label("emails"),
                func.coalesce(func.sum(UsageCostRollup.total_minutes), 0).label("minutes"),
                func.coalesce(func.sum(UsageCostRollup.total_cost_amount), 0).label("cost"),
            )
            .where(*filters)
            .group_by(UsageCostRollup.channel)
        )
        rows = result.all()

    channels = [
        ChannelUsage(
            channel=r.channel,
            event_count=int(r.event_count),
            total_segments=int(r.segments),
            total_dials=int(r.dials),
            total_emails=int(r.emails),
            total_minutes=float(r.minutes),
            total_cost=float(r.cost),
        )
        for r in rows
    ]
    total_cost = sum(c.total_cost for c in channels)
    return UsageSummary(
        start_date=start, end_date=end, currency="USD",
        total_cost=total_cost, channels=channels,
    )


@router.get("/by-campaign", response_model=CampaignUsageReport)
@limiter.limit(RATE_READ)
async def get_usage_by_campaign(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    start_date: date | None = Query(None, description="Inclusive range start (YYYY-MM-DD)"),
    end_date: date | None = Query(None, description="Inclusive range end (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=200),
) -> CampaignUsageReport:
    """Top workflows by spend over the range, from raw usage_events (workflow_id-tagged)."""
    if not current_user.institution_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User is not associated with an institution")

    institution_id = current_user.institution_id
    today = date.today()
    start, end = _resolve_window(start_date, end_date, today)
    # usage_events.occurred_at is a timestamp; bracket by [start, end+1day).
    end_cap = end + timedelta(days=1)

    async with get_db_session() as session:
        result = await session.execute(
            select(
                UsageEvent.workflow_id,
                func.count().label("event_count"),
                func.coalesce(func.sum(UsageEvent.cost_amount), 0).label("cost"),
                func.coalesce(func.sum(UsageEvent.segments), 0).label("segments"),
                func.coalesce(func.sum(UsageEvent.minutes), 0).label("minutes"),
                func.coalesce(func.sum(UsageEvent.emails), 0).label("emails"),
            )
            .where(
                UsageEvent.institution_id == institution_id,
                UsageEvent.workflow_id.is_not(None),
                UsageEvent.occurred_at >= start,
                UsageEvent.occurred_at < end_cap,
            )
            .group_by(UsageEvent.workflow_id)
            .order_by(func.coalesce(func.sum(UsageEvent.cost_amount), 0).desc())
            .limit(limit)
        )
        rows = result.all()

    campaigns = [
        CampaignUsage(
            workflow_id=str(r.workflow_id),
            event_count=int(r.event_count),
            total_cost=float(r.cost),
            total_segments=int(r.segments),
            total_minutes=float(r.minutes),
            total_emails=int(r.emails),
        )
        for r in rows
    ]
    return CampaignUsageReport(start_date=start, end_date=end, campaigns=campaigns)
