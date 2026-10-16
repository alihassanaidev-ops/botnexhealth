"""Institution-level campaign analytics rollups."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.app.api.deps import get_current_institution_or_location_admin
from src.app.api.routes.automation_workflows import (
    CampaignAnalyticsResponse,
    CampaignRevenueResponse,
)
from src.app.database import get_db_session
from src.app.models.user import User
from src.app.services.automation.campaign_analytics_service import (
    CampaignAnalyticsService,
    resolve_window,
)

router = APIRouter(prefix="/automation/campaign-analytics", tags=["Campaign Analytics"])

_InstitutionOrLocationAdmin = Annotated[User, Depends(get_current_institution_or_location_admin)]


class CampaignAnalyticsRollupResponse(BaseModel):
    start_date: str
    end_date: str
    campaigns: list[CampaignAnalyticsResponse] = Field(default_factory=list)
    #: Revenue summed across the returned campaigns, for a dashboard headline.
    totals: CampaignRevenueResponse | None = None


def _totals(campaigns: list[CampaignAnalyticsResponse]) -> CampaignRevenueResponse | None:
    revenues = [c.revenue for c in campaigns if c.revenue is not None]
    if not revenues:
        return None
    return CampaignRevenueResponse(
        currency=revenues[0].currency,
        generated=round(sum(r.generated for r in revenues), 2),
        protected=round(sum(r.protected for r in revenues), 2),
        total=round(sum(r.total for r in revenues), 2),
        spend=round(sum(r.spend for r in revenues), 2),
        roi=round(sum(r.roi for r in revenues), 2),
        configured=any(r.configured for r in revenues),
        rule=revenues[0].rule,
        value_source=None,
    )


def _institution_id(user: User) -> str:
    if not user.institution_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No institution context"
        )
    return str(user.institution_id)


@router.get("", response_model=CampaignAnalyticsRollupResponse)
async def list_campaign_analytics(
    current_user: _InstitutionOrLocationAdmin,
    start_date: date | None = Query(None, description="Inclusive range start (YYYY-MM-DD)"),
    end_date: date | None = Query(None, description="Inclusive range end (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=200),
    location_id: str | None = Query(
        None, description="Restrict to one clinic's own campaigns."
    ),
) -> CampaignAnalyticsRollupResponse:
    """Return campaign analytics summaries across workflows for the institution."""
    inst_id = _institution_id(current_user)
    try:
        start, end = resolve_window(start_date, end_date)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    async with get_db_session() as session:
        campaigns = await CampaignAnalyticsService(session).campaign_rollups(
            institution_id=inst_id,
            start_date=start,
            end_date=end,
            limit=limit,
            location_id=location_id,
        )
    responses = [
        CampaignAnalyticsResponse.from_service(campaign) for campaign in campaigns
    ]
    return CampaignAnalyticsRollupResponse(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        campaigns=responses,
        totals=_totals(responses),
    )
