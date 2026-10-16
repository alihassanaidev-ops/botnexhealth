"""Task 12 — revenue attributed to a campaign."""

from __future__ import annotations

from src.app.services.automation.campaign_revenue_service import (
    ATTRIBUTION_RULE,
    attribution_for,
    compute_campaign_revenue,
)

VALUES = {"avg_appointment_value": 200.0}


def _revenue(category, summary, values=VALUES, spend=0.0):
    return compute_campaign_revenue(
        category=category,
        summary=summary,
        values=values,
        value_source="location" if values else None,
        currency="USD",
        spend=spend,
    )


def test_confirmation_is_protected_revenue():
    out = _revenue("appointment_confirmation", {"confirmed": 10, "booked": 0})
    assert out.protected == 2000.0
    assert out.generated == 0.0
    assert out.total == 2000.0


def test_reminder_ops_is_protected_revenue():
    out = _revenue("appointment_ops", {"confirmed": 4, "booked": 0})
    assert out.protected == 800.0
    assert out.generated == 0.0


def test_recall_is_generated_revenue():
    out = _revenue("recall", {"booked": 3, "confirmed": 0})
    assert out.generated == 600.0
    assert out.protected == 0.0


def test_sales_is_generated_revenue():
    out = _revenue("sales", {"booked": 5})
    assert out.generated == 1000.0


def test_callback_category_is_not_revenue():
    out = _revenue("callback", {"booked": 9, "confirmed": 9})
    assert out.total == 0.0
    assert attribution_for("callback") == "none"


def test_roi_subtracts_spend():
    out = _revenue("recall", {"booked": 10}, spend=250.0)
    assert out.total == 2000.0
    assert out.roi == 1750.0


def test_unconfigured_values_report_not_configured():
    out = _revenue("recall", {"booked": 10}, values=None)
    assert out.configured is False
    assert out.total == 0.0
    assert out.value_source is None


def test_rule_is_always_returned_with_the_figure():
    out = _revenue("recall", {"booked": 1})
    assert out.rule == ATTRIBUTION_RULE


def test_value_source_is_carried_through():
    out = _revenue("recall", {"booked": 1})
    assert out.value_source == "location"
