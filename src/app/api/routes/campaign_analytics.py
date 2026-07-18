"""Institution-level campaign analytics rollups."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.app.api.deps import get_current_institution_or_location_admin
from src.app.api.routes.automation_workflows import CampaignAnalyticsResponse
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
        )
    return CampaignAnalyticsRollupResponse(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        campaigns=[
            CampaignAnalyticsResponse.from_service(campaign)
            for campaign in campaigns
        ],
    )
