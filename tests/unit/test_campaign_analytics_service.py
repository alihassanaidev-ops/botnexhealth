"""Unit contracts for campaign analytics taxonomy and rollup SQL."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.app.models.automation_workflow import (
    AutomationWorkflow,
    AutomationWorkflowVersion,
)
from src.app.models.campaign_analytics import CampaignMetricsDaily
from src.app.models.usage_cost_rollup import NULL_LOCATION_SENTINEL
from src.app.services.automation import campaign_analytics_service as analytics
from src.app.services.automation import campaign_templates


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
    labels = [
        definition.label for definition in analytics.outcome_definitions(category)
    ]
    assert labels[:2] == ["Confirmed", "Reschedule Requested"]


def test_recall_category_uses_booking_as_success_label() -> None:
    wf = _workflow(name="Recall Hygiene", category="recall", trigger="recall_scan")

    definitions = analytics.outcome_definitions(analytics.campaign_category(wf))

    assert definitions[0].key == "booked"
    assert definitions[0].label == "Recall Booked"


def test_callback_category_exposes_voice_outcome_labels() -> None:
    wf = _workflow(
        name="Callback Automation", category="callback", trigger="callback_requested"
    )

    definitions = analytics.outcome_definitions(analytics.campaign_category(wf))
    labels_by_key = {definition.key: definition.label for definition in definitions}

    assert labels_by_key["voice_answered"] == "Answered"
    assert labels_by_key["voice_failed"] == "Unreachable"
    assert labels_by_key["opt_out"] == "Do-Not-Call"
    assert labels_by_key["transferred"] == "Transferred"


def test_sales_category_exposes_qualification_outcome_labels() -> None:
    wf = _workflow(
        name="Sales Qualification", category="sales", trigger="enquiry_received"
    )

    definitions = analytics.outcome_definitions(analytics.campaign_category(wf))
    labels_by_key = {definition.key: definition.label for definition in definitions}

    assert labels_by_key["qualified"] == "Qualified"
    assert labels_by_key["booked"] == "Booked"
    assert labels_by_key["not_qualified"] == "Not Qualified"
    assert labels_by_key["unreachable"] == "Unreachable"


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


def test_every_outcome_key_is_a_real_rollup_column() -> None:
    """An outcome nobody rolls up reads as a real zero, not as a missing figure.

    The sales vocabulary named ``qualified`` long before any branch produced it,
    so every sales campaign reported "0 qualified" with nothing to say otherwise.
    """
    metric_columns = set(analytics.ROLLUP_METRIC_COLUMNS)
    for category, definitions in analytics._OUTCOME_DEFINITIONS.items():
        for definition in definitions:
            assert definition.key in metric_columns, (
                f"outcome {definition.key!r} in category {category!r} has no rollup "
                "column, so it can only ever report zero"
            )


def test_every_template_exit_outcome_is_accounted_for() -> None:
    """The other half of the coverage check, walked from the templates in.

    ``test_every_outcome_key_is_a_real_rollup_column`` asks whether every label
    on the screen has a column behind it. It does not ask whether the strings
    campaigns actually exit with reach those columns, and they did not: the
    pre-appointment template exits ``appointment_confirmed``, the rollup looked
    for ``confirmed``, and a campaign with 38 confirmed runs reported none of
    them. Anything a template can exit with has to be either mapped or named as
    deliberately unrolled.
    """
    mapped = {
        outcome
        for outcomes in analytics._TERMINAL_OUTCOMES.values()
        for outcome in outcomes
    }
    for template in campaign_templates.list_templates():
        for node in template.definition.get("nodes", ()):
            if node.get("type") != "exit":
                continue
            outcome = node.get("outcome")
            if outcome is None:
                continue
            assert (
                outcome in mapped or outcome in analytics.UNROLLED_TERMINAL_OUTCOMES
            ), (
                f"template {template.id!r} exits with outcome {outcome!r}, which "
                "no rollup branch counts and which is not listed in "
                "UNROLLED_TERMINAL_OUTCOMES, so it reports as a silent zero"
            )


def test_confirmed_counts_the_outcome_the_preappointment_template_exits_with() -> None:
    """The regression that staging surfaced: prefixed names were not mapped."""
    assert "appointment_confirmed" in analytics._TERMINAL_OUTCOMES["confirmed"]
    assert (
        "appointment_rescheduled"
        in analytics._TERMINAL_OUTCOMES["reschedule_requested"]
    )
    assert (
        "callback_requested_after_max_attempts"
        in analytics._TERMINAL_OUTCOMES["callback_requested"]
    )
    assert "'appointment_confirmed'" in str(analytics._INSERT_ROLLUP_SQL.text)


def test_qualified_counts_the_outcome_the_sales_template_exits_with() -> None:
    sql = str(analytics._INSERT_ROLLUP_SQL.text)

    assert "'qualified', 'qualified_booking_link_sent'" in sql
    assert analytics._TERMINAL_OUTCOMES["qualified"] == (
        "qualified",
        "qualified_booking_link_sent",
    )


def test_response_events_do_not_recount_a_run_its_outcome_already_claimed() -> None:
    """A link booking writes a response event *and* exits the run ``booked``."""
    sql = str(analytics._INSERT_ROLLUP_SQL.text)

    booked = analytics._response_filter("booked")
    assert booked in sql
    assert "COUNT(DISTINCT r.id)" in booked
    assert (
        "r.outcome IS NULL OR r.outcome NOT IN "
        "('booked', 'appointment_booked', 'callback_booked')" in booked
    )


def test_terminal_outcomes_are_dated_by_completion_not_enrolment() -> None:
    """Both halves of a deduped outcome must land in the same recompute window."""
    sql = str(analytics._INSERT_ROLLUP_SQL.text)

    assert "COALESCE(r.completed_at, r.created_at)" in sql


def test_metric_select_list_fills_missing_columns_by_name() -> None:
    rendered = analytics._metric_select_list({"booked": "COUNT(*)::bigint"})

    assert "COUNT(*)::bigint AS booked" in rendered
    assert "0::bigint AS qualified" in rendered
    assert "0::numeric(16, 5) AS total_cost" in rendered
    assert rendered.count(" AS ") == len(analytics.ROLLUP_METRIC_COLUMNS)


def test_metric_select_list_rejects_a_column_that_is_not_rolled_up() -> None:
    with pytest.raises(ValueError, match="revenue_attributed"):
        analytics._metric_select_list({"revenue_attributed": "COUNT(*)::bigint"})
