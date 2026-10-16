"""Rollup revenue totals for the dashboard headline (Task 12)."""

from __future__ import annotations

from datetime import datetime, timezone

from src.app.api.routes.automation_workflows import (
    CampaignAnalyticsResponse,
    CampaignRevenueResponse,
)
from src.app.api.routes.campaign_analytics import _totals


def _response(revenue: CampaignRevenueResponse | None) -> CampaignAnalyticsResponse:
    return CampaignAnalyticsResponse(
        workflow_id="wf-1",
        workflow_name="Recall",
        category="recall",
        start_date="2026-10-01",
        end_date="2026-10-15",
        summary={"booked": 1},
        channels=[],
        outcomes=[],
        trend=[],
        cost={
            "currency": "USD",
            "total_cost": 0.0,
            "cost_per_booking": None,
            "cost_per_confirmation": None,
        },
        generated_at=datetime.now(timezone.utc),
        rollup_fresh_at=None,
        revenue=revenue,
    )


def _revenue(generated: float, protected: float, configured: bool = True):
    total = generated + protected
    return CampaignRevenueResponse(
        currency="USD",
        generated=generated,
        protected=protected,
        total=total,
        spend=0.0,
        roi=total,
        configured=configured,
        rule="rule",
        value_source="location",
    )


def test_totals_sum_across_campaigns():
    totals = _totals([_response(_revenue(600.0, 0.0)), _response(_revenue(0.0, 400.0))])
    assert totals is not None
    assert totals.generated == 600.0
    assert totals.protected == 400.0
    assert totals.total == 1000.0


def test_totals_none_when_no_revenue():
    assert _totals([_response(None)]) is None
    assert _totals([]) is None


def test_totals_configured_if_any_campaign_is_configured():
    totals = _totals(
        [_response(_revenue(0.0, 0.0, configured=False)), _response(_revenue(1.0, 0.0))]
    )
    assert totals is not None and totals.configured is True
