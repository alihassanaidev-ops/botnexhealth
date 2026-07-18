"""Unit contracts for campaign analytics taxonomy and rollup SQL."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.app.models.automation_workflow import AutomationWorkflow, AutomationWorkflowVersion
from src.app.models.campaign_analytics import CampaignMetricsDaily
from src.app.models.usage_cost_rollup import NULL_LOCATION_SENTINEL
from src.app.services.automation import campaign_analytics_service as analytics


def _workflow(
    *,
    name: str = "Appointment Confirmation",
    category: str | None = "appointment_ops",
    trigger: str = "appointment_offset",
) -> AutomationWorkflow:
    version = AutomationWorkflowVersion(
        id="ver-1",
        institution_id="inst-1",
        workflow_id="wf-1",
        version_number=1,
        definition={
            "trigger": {"type": trigger},
            "nodes": [{"type": "exit", "id": "done", "outcome": "confirmed"}],
        },
    )
    wf = AutomationWorkflow(
        id="wf-1",
        institution_id="inst-1",
        name=name,
        category=category,
    )
    wf.current_version = version
    return wf


def test_appointment_confirmation_category_uses_specific_outcomes() -> None:
    category = analytics.campaign_category(_workflow())

    assert category == "appointment_confirmation"
    labels = [definition.label for definition in analytics.outcome_definitions(category)]
    assert labels[:2] == ["Confirmed", "Reschedule Requested"]


def test_recall_category_uses_booking_as_success_label() -> None:
    wf = _workflow(name="Recall Hygiene", category="recall", trigger="recall_scan")

    definitions = analytics.outcome_definitions(analytics.campaign_category(wf))

    assert definitions[0].key == "booked"
    assert definitions[0].label == "Recall Booked"


def test_callback_category_exposes_voice_outcome_labels() -> None:
    wf = _workflow(name="Callback Automation", category="callback", trigger="callback_requested")

    definitions = analytics.outcome_definitions(analytics.campaign_category(wf))
    labels_by_key = {definition.key: definition.label for definition in definitions}

    assert labels_by_key["voice_answered"] == "Answered"
    assert labels_by_key["voice_failed"] == "Unreachable"
    assert labels_by_key["opt_out"] == "Do-Not-Call"
    assert labels_by_key["transferred"] == "Transferred"


def test_rollup_sql_covers_every_metrics_model_column() -> None:
    sql = str(analytics._INSERT_ROLLUP_SQL.text)
    for column in analytics.ROLLUP_METRIC_COLUMNS:
        assert column in sql

    model_columns = {column.name for column in CampaignMetricsDaily.__table__.columns}
    non_metric_columns = {
        "institution_id",
        "location_id",
        "workflow_id",
        "workflow_version_id",
        "metric_date",
        "cost_per_booking",
        "cost_per_confirmation",
        "currency",
        "updated_at",
    }
    assert set(analytics.ROLLUP_METRIC_COLUMNS) | non_metric_columns == model_columns
    assert ":null_location_sentinel" in sql
    assert NULL_LOCATION_SENTINEL not in sql


def test_recompute_window_rejects_inverted_window() -> None:
    async def go() -> str | None:
        try:
            await analytics.recompute_window(
                AsyncMock(), start_date=date(2026, 7, 2), end_date=date(2026, 7, 1)
            )
        except ValueError as exc:
            return str(exc)
        return None

    err = asyncio.run(go())
    assert err and "start_date" in err


def test_resolve_window_rejects_too_large_range() -> None:
    with pytest.raises(ValueError, match="731"):
        analytics.resolve_window(
            date(2024, 1, 1),
            date(2026, 7, 1),
            today=date(2026, 7, 1),
        )
