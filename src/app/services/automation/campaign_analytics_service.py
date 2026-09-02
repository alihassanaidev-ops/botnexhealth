"""Campaign outcome analytics rollup and read models."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.automation_workflow import AutomationWorkflow
from src.app.models.campaign_analytics import CampaignMetricsDaily
from src.app.models.usage_cost_rollup import NULL_LOCATION_SENTINEL

logger = logging.getLogger(__name__)


ROLLUP_METRIC_COLUMNS = (
    "enrollments",
    "active",
    "completed",
    "failed",
    "cancelled",
    "suppressed",
    "sms_sent",
    "sms_delivered",
    "sms_failed",
    "sms_replied",
    "voice_attempted",
    "voice_answered",
    "voice_voicemail",
    "voice_failed",
    "email_sent",
    "email_delivered",
    "email_opened",
    "email_clicked",
    "email_bounced",
    "confirmed",
    "booked",
    "reschedule_requested",
    "callback_requested",
    "staff_handoff",
    "opt_out",
    "qualified",
    "not_qualified",
    "unreachable",
    "transferred",
    "total_cost",
)


@dataclass(frozen=True)
class OutcomeDefinition:
    key: str
    label: str
    group: str
    description: str
    sort_order: int


@dataclass(frozen=True)
class ChannelAnalytics:
    channel: str
    attempted: int
    delivered: int
    failed: int
    responded: int = 0


@dataclass(frozen=True)
class OutcomeAnalytics:
    key: str
    label: str
    group: str
    count: int
    rate: float | None
    description: str


@dataclass(frozen=True)
class TrendPoint:
    date: date
    enrollments: int
    sends: int
    responses: int
    confirmed: int
    booked: int
    handoffs: int
    total_cost: float


@dataclass(frozen=True)
class CostSummary:
    currency: str
    total_cost: float
    cost_per_booking: float | None
    cost_per_confirmation: float | None


@dataclass(frozen=True)
class CampaignAnalytics:
    workflow_id: str
    workflow_name: str
    category: str
    start_date: date
    end_date: date
    summary: dict[str, int]
    channels: list[ChannelAnalytics]
    outcomes: list[OutcomeAnalytics]
    trend: list[TrendPoint]
    cost: CostSummary
    generated_at: datetime
    rollup_fresh_at: datetime | None


_ZERO_METRICS: dict[str, int | Decimal] = {
    column: Decimal("0") if column == "total_cost" else 0
    for column in ROLLUP_METRIC_COLUMNS
}

_OUTCOME_DEFINITIONS: dict[str, tuple[OutcomeDefinition, ...]] = {
    "appointment_confirmation": (
        OutcomeDefinition(
            "confirmed",
            "Confirmed",
            "success",
            "Patient confirmed the appointment.",
            10,
        ),
        OutcomeDefinition(
            "reschedule_requested",
            "Reschedule Requested",
            "neutral",
            "Patient asked to move the appointment.",
            20,
        ),
        OutcomeDefinition(
            "staff_handoff",
            "Staff Handoff",
            "neutral",
            "Automation routed the run to staff.",
            30,
        ),
        OutcomeDefinition("opt_out", "Opt-Out", "failure", "Patient opted out.", 40),
    ),
    "appointment_ops": (
        OutcomeDefinition(
            "completed",
            "Completed Runs",
            "success",
            "Campaign runs that completed.",
            10,
        ),
        OutcomeDefinition(
            "failed", "Failed Runs", "failure", "Runs that failed or blocked.", 20
        ),
        OutcomeDefinition(
            "cancelled",
            "Cancelled Runs",
            "neutral",
            "Runs cancelled before completion.",
            30,
        ),
        OutcomeDefinition(
            "staff_handoff",
            "Staff Handoff",
            "neutral",
            "Automation routed the run to staff.",
            40,
        ),
        OutcomeDefinition("opt_out", "Opt-Out", "failure", "Patient opted out.", 50),
    ),
    "recall": (
        OutcomeDefinition(
            "booked",
            "Recall Booked",
            "success",
            "Patient booked from recall outreach.",
            10,
        ),
        OutcomeDefinition(
            "callback_requested",
            "Callback Requested",
            "neutral",
            "Patient asked for staff follow-up.",
            20,
        ),
        OutcomeDefinition(
            "staff_handoff",
            "Staff Handoff",
            "neutral",
            "Automation routed the run to staff.",
            30,
        ),
        OutcomeDefinition("opt_out", "Opt-Out", "failure", "Patient opted out.", 40),
    ),
    "callback": (
        OutcomeDefinition(
            "callback_requested",
            "Callbacks Automated",
            "neutral",
            "Callback requests entering the campaign.",
            10,
        ),
        OutcomeDefinition(
            "voice_answered",
            "Answered",
            "success",
            "AI voice callback reached the patient.",
            20,
        ),
        OutcomeDefinition(
            "booked",
            "Booked By Callback",
            "success",
            "Callback outreach produced a booking.",
            30,
        ),
        OutcomeDefinition(
            "transferred",
            "Transferred",
            "neutral",
            "AI voice transferred the call to staff.",
            40,
        ),
        OutcomeDefinition(
            "staff_handoff",
            "Staff Handoff",
            "neutral",
            "Automation routed the run to staff.",
            50,
        ),
        OutcomeDefinition(
            "voice_failed",
            "Unreachable",
            "failure",
            "Callback voice attempts did not reach the patient.",
            60,
        ),
        OutcomeDefinition(
            "opt_out", "Do-Not-Call", "failure", "Patient asked not to be called.", 70
        ),
    ),
    "treatment": (
        OutcomeDefinition(
            "booked",
            "Treatment Visit Booked",
            "success",
            "Patient scheduled the next treatment visit.",
            10,
        ),
        OutcomeDefinition(
            "callback_requested",
            "Callback Requested",
            "neutral",
            "Patient asked for staff follow-up.",
            20,
        ),
        OutcomeDefinition(
            "staff_handoff",
            "Staff Handoff",
            "neutral",
            "Automation routed the run to staff.",
            30,
        ),
        OutcomeDefinition("opt_out", "Opt-Out", "failure", "Patient opted out.", 40),
    ),
    "reactivation": (
        OutcomeDefinition(
            "booked",
            "Reactivation Booked",
            "success",
            "Patient scheduled a visit after reactivation outreach.",
            10,
        ),
        OutcomeDefinition(
            "callback_requested",
            "Callback Requested",
            "neutral",
            "Patient asked for staff follow-up.",
            20,
        ),
        OutcomeDefinition(
            "staff_handoff",
            "Staff Handoff",
            "neutral",
            "Automation routed the run to staff.",
            30,
        ),
        OutcomeDefinition("opt_out", "Opt-Out", "failure", "Patient opted out.", 40),
    ),
    "sales": (
        OutcomeDefinition(
            "qualified",
            "Qualified",
            "success",
            "Lead qualified and received a booking path.",
            10,
        ),
        OutcomeDefinition(
            "booked", "Booked", "success", "Lead booked an appointment.", 20
        ),
        OutcomeDefinition(
            "not_qualified",
            "Not Qualified",
            "failure",
            "Lead was not a fit for this campaign.",
            30,
        ),
        OutcomeDefinition(
            "staff_handoff",
            "Staff Handoff",
            "neutral",
            "Automation routed the lead to staff.",
            40,
        ),
        OutcomeDefinition(
            "unreachable",
            "Unreachable",
            "failure",
            "Lead did not complete qualification.",
            50,
        ),
        OutcomeDefinition("opt_out", "Opt-Out", "failure", "Lead opted out.", 60),
    ),
    "default": (
        OutcomeDefinition(
            "confirmed", "Confirmed", "success", "Patient confirmed.", 10
        ),
        OutcomeDefinition("booked", "Booked", "success", "Patient booked.", 20),
        OutcomeDefinition(
            "callback_requested",
            "Callback Requested",
            "neutral",
            "Patient asked for a callback.",
            30,
        ),
        OutcomeDefinition(
            "staff_handoff",
            "Staff Handoff",
            "neutral",
            "Automation routed the run to staff.",
            40,
        ),
        OutcomeDefinition("opt_out", "Opt-Out", "failure", "Patient opted out.", 50),
    ),
}


_DELETE_ROLLUP_SQL = text(
    """
    DELETE FROM campaign_metrics_daily
    WHERE metric_date >= :start_date
      AND metric_date <= :end_date
    """
)


# Run outcomes that count toward a metric column, keyed by that column. These are
# the terminal ``outcome`` values campaign templates exit with; the response
# vocabularies below are what a patient action records while a run is still live.
_TERMINAL_OUTCOMES: dict[str, tuple[str, ...]] = {
    "confirmed": ("confirmed", "confirmed_by_reply"),
    "booked": ("booked", "appointment_booked", "callback_booked"),
    "reschedule_requested": ("reschedule_requested", "skipped_rescheduled"),
    "callback_requested": ("callback_requested", "patient_asks_for_staff"),
    "opt_out": (
        "opt_out",
        "unsubscribed",
        "sms_opt_out",
        "do_not_contact",
        "do_not_call",
    ),
    "qualified": ("qualified", "qualified_booking_link_sent"),
    "not_qualified": ("not_qualified", "not_a_fit", "declined"),
    "unreachable": ("unreachable", "unreachable_after_max_attempts"),
}

# What a response event has to look like to count toward the same metric. A run
# reaching its terminal outcome and the patient action that caused it are two
# records of one event, so these only count for runs whose own outcome has not
# already claimed the metric.
_RESPONSE_PREDICATES: dict[str, str] = {
    "confirmed": (
        "e.normalized_outcome IN ('confirmed', 'confirmed_by_reply')"
        " OR e.normalized_intent = 'confirm'"
    ),
    "booked": (
        "e.normalized_outcome IN ('booked', 'appointment_booked', 'callback_booked')"
        " OR e.normalized_intent = 'booked'"
    ),
    "reschedule_requested": (
        "e.normalized_intent = 'reschedule_requested'"
        " OR e.normalized_outcome = 'reschedule_requested'"
    ),
    "callback_requested": (
        "e.normalized_intent IN ('callback_requested', 'staff_requested')"
        " OR e.normalized_outcome IN ('callback_requested', 'patient_asks_for_staff')"
    ),
    "opt_out": (
        "e.normalized_intent IN ('opt_out', 'stop', 'unsubscribe')"
        " OR e.normalized_outcome IN ('opt_out', 'unsubscribed')"
    ),
}


def _sql_string_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _terminal_filter(column: str) -> str:
    """Count each run once, on the day it reached the outcome."""
    outcomes = _sql_string_list(_TERMINAL_OUTCOMES[column])
    return f"COUNT(*) FILTER (WHERE r.outcome IN ({outcomes}))::bigint"


def _response_filter(column: str) -> str:
    """Count a responding run only where its own outcome has not already."""
    outcomes = _sql_string_list(_TERMINAL_OUTCOMES[column])
    return (
        "COUNT(DISTINCT r.id) FILTER ("
        f" WHERE ({_RESPONSE_PREDICATES[column]})"
        f" AND (r.outcome IS NULL OR r.outcome NOT IN ({outcomes}))"
        ")::bigint"
    )


def _metric_select_list(expressions: dict[str, str]) -> str:
    """Render every metric column for one branch of the rollup union.

    A branch supplies only the metrics it measures and this fills the rest with
    zeros *by name*. The union used to be written positionally, which is how
    ``qualified`` came to be named in the sales outcome vocabulary while no branch
    ever produced it — it read as a real zero on every sales campaign rather than
    as a missing figure.
    """
    unknown = sorted(set(expressions) - set(ROLLUP_METRIC_COLUMNS))
    if unknown:
        raise ValueError(f"unknown rollup metric columns: {unknown}")
    rendered = []
    for column in ROLLUP_METRIC_COLUMNS:
        zero = "0::numeric(16, 5)" if column == "total_cost" else "0::bigint"
        rendered.append(f"{expressions.get(column, zero)} AS {column}")
    return ",\n            ".join(rendered)


_ROLLED_SUMS = ",\n            ".join(
    f"SUM({column})::numeric(16, 5) AS {column}"
    if column == "total_cost"
    else f"SUM({column})::bigint AS {column}"
    for column in ROLLUP_METRIC_COLUMNS
)

_METRIC_COLUMN_LIST = ", ".join(ROLLUP_METRIC_COLUMNS)

_LOC = "CAST(:null_location_sentinel AS uuid)"
_DAY = "AT TIME ZONE 'UTC')::date"


_RUN_ENROLLMENT_BRANCH = f"""
        SELECT
            r.institution_id,
            COALESCE(r.location_id, {_LOC}) AS location_id,
            r.workflow_id,
            r.workflow_version_id,
            (r.created_at {_DAY} AS metric_date,
            {_metric_select_list({
                "enrollments": "COUNT(*)::bigint",
                "active": (
                    "COUNT(*) FILTER ("
                    " WHERE r.status IN ('pending', 'running', 'waiting')"
                    ")::bigint"
                ),
                "completed": "COUNT(*) FILTER (WHERE r.status = 'completed')::bigint",
                "failed": (
                    "COUNT(*) FILTER (WHERE r.status IN ('failed', 'blocked'))::bigint"
                ),
                "cancelled": "COUNT(*) FILTER (WHERE r.status = 'cancelled')::bigint",
                "suppressed": (
                    "COUNT(*) FILTER ("
                    " WHERE r.outcome IN"
                    " ('suppressed', 'skipped_suppressed', 'compliance_hold')"
                    " OR r.blocked_reason ILIKE '%suppression%'"
                    " OR r.blocked_reason ILIKE '%consent%'"
                    " OR r.blocked_reason ILIKE '%do not contact%'"
                    ")::bigint"
                ),
            })},
            'USD'::varchar(3) AS currency
        FROM automation_workflow_runs r
        WHERE (r.created_at {_DAY} >= :start_date
          AND (r.created_at {_DAY} <= :end_date
        GROUP BY 1, 2, 3, 4, 5
"""


# Terminal outcomes are attributed to the day the run reached them, not the day it
# enrolled. Without that split the response branch below could not tell an
# already-counted run from one still in flight without losing the count whenever
# the two dates fell in different recompute windows.
_RUN_OUTCOME_BRANCH = f"""
        SELECT
            r.institution_id,
            COALESCE(r.location_id, {_LOC}) AS location_id,
            r.workflow_id,
            r.workflow_version_id,
            (COALESCE(r.completed_at, r.created_at) {_DAY} AS metric_date,
            {_metric_select_list({
                column: _terminal_filter(column) for column in _TERMINAL_OUTCOMES
            })},
            'USD'::varchar(3) AS currency
        FROM automation_workflow_runs r
        WHERE r.outcome IS NOT NULL
          AND (COALESCE(r.completed_at, r.created_at) {_DAY} >= :start_date
          AND (COALESCE(r.completed_at, r.created_at) {_DAY} <= :end_date
        GROUP BY 1, 2, 3, 4, 5
"""


_SMS_BRANCH = f"""
        SELECT
            r.institution_id,
            COALESCE(s.location_id, r.location_id, {_LOC}) AS location_id,
            r.workflow_id,
            r.workflow_version_id,
            (s.timestamp {_DAY} AS metric_date,
            {_metric_select_list({
                "sms_sent": (
                    "COUNT(*) FILTER (WHERE s.status NOT IN ('suppressed'))::bigint"
                ),
                "sms_delivered": (
                    "COUNT(*) FILTER ("
                    " WHERE s.status = 'delivered' OR s.provider_status = 'delivered'"
                    ")::bigint"
                ),
                "sms_failed": (
                    "COUNT(*) FILTER ("
                    " WHERE s.status = 'failed'"
                    " OR s.provider_status IN ('failed', 'undelivered')"
                    ")::bigint"
                ),
            })},
            'USD'::varchar(3) AS currency
        FROM sms_history_logs s
        JOIN automation_workflow_runs r ON r.id = s.workflow_run_id
        WHERE s.workflow_run_id IS NOT NULL
          AND (s.timestamp {_DAY} >= :start_date
          AND (s.timestamp {_DAY} <= :end_date
        GROUP BY 1, 2, 3, 4, 5
"""


_VOICE_BRANCH = f"""
        SELECT
            r.institution_id,
            COALESCE(v.location_id, r.location_id, {_LOC}) AS location_id,
            r.workflow_id,
            r.workflow_version_id,
            (v.created_at {_DAY} AS metric_date,
            {_metric_select_list({
                "voice_attempted": "COUNT(*)::bigint",
                "voice_answered": (
                    "COUNT(*) FILTER ("
                    " WHERE v.dial_outcome IN ('answered', 'transferred')"
                    ")::bigint"
                ),
                "voice_voicemail": (
                    "COUNT(*) FILTER (WHERE v.dial_outcome = 'voicemail')::bigint"
                ),
                "voice_failed": (
                    "COUNT(*) FILTER ("
                    " WHERE v.status = 'failed'"
                    " OR v.dial_outcome IN ('failed', 'no_answer', 'busy', 'unknown')"
                    ")::bigint"
                ),
                # Per run, not per attempt: a callback dialled three times and
                # transferred once is one transferred callback.
                "transferred": (
                    "COUNT(DISTINCT v.workflow_run_id) FILTER ("
                    " WHERE v.dial_outcome = 'transferred'"
                    ")::bigint"
                ),
            })},
            'USD'::varchar(3) AS currency
        FROM workflow_voice_attempts v
        JOIN automation_workflow_runs r ON r.id = v.workflow_run_id
        WHERE (v.created_at {_DAY} >= :start_date
          AND (v.created_at {_DAY} <= :end_date
        GROUP BY 1, 2, 3, 4, 5
"""


_USAGE_BRANCH = f"""
        SELECT
            r.institution_id,
            COALESCE(u.location_id, r.location_id, {_LOC}) AS location_id,
            r.workflow_id,
            r.workflow_version_id,
            (u.occurred_at {_DAY} AS metric_date,
            {_metric_select_list({
                "email_sent": (
                    "COALESCE("
                    " SUM(u.emails), COUNT(*) FILTER (WHERE u.channel = 'email')"
                    ")::bigint"
                ),
                "email_delivered": (
                    "COUNT(*) FILTER ("
                    " WHERE u.channel = 'email' AND u.direction = 'outbound'"
                    ")::bigint"
                ),
                "total_cost": "COALESCE(SUM(u.cost_amount), 0)::numeric(16, 5)",
            })},
            COALESCE(MAX(u.currency), 'USD')::varchar(3) AS currency
        FROM usage_events u
        JOIN automation_workflow_runs r ON r.id = u.workflow_run_id
        WHERE u.workflow_run_id IS NOT NULL
          AND (u.occurred_at {_DAY} >= :start_date
          AND (u.occurred_at {_DAY} <= :end_date
        GROUP BY 1, 2, 3, 4, 5
"""


_RESPONSE_BRANCH = f"""
        SELECT
            r.institution_id,
            COALESCE(e.location_id, r.location_id, {_LOC}) AS location_id,
            r.workflow_id,
            r.workflow_version_id,
            (e.occurred_at {_DAY} AS metric_date,
            {_metric_select_list({
                "sms_replied": "COUNT(*) FILTER (WHERE e.channel = 'sms')::bigint",
                "email_opened": (
                    "COUNT(*) FILTER ("
                    " WHERE e.channel = 'email' AND e.normalized_intent = 'opened'"
                    ")::bigint"
                ),
                "email_clicked": (
                    "COUNT(*) FILTER ("
                    " WHERE e.channel = 'email' AND e.normalized_intent = 'clicked'"
                    ")::bigint"
                ),
                "email_bounced": (
                    "COUNT(*) FILTER ("
                    " WHERE e.channel = 'email'"
                    " AND e.normalized_intent IN ('bounced', 'failed')"
                    ")::bigint"
                ),
                **{
                    column: _response_filter(column)
                    for column in _RESPONSE_PREDICATES
                },
            })},
            'USD'::varchar(3) AS currency
        FROM campaign_response_events e
        JOIN automation_workflow_runs r ON r.id = e.workflow_run_id
        WHERE e.workflow_run_id IS NOT NULL
          AND (e.occurred_at {_DAY} >= :start_date
          AND (e.occurred_at {_DAY} <= :end_date
        GROUP BY 1, 2, 3, 4, 5
"""


_HANDOFF_BRANCH = f"""
        SELECT
            r.institution_id,
            COALESCE(h.location_id, r.location_id, {_LOC}) AS location_id,
            r.workflow_id,
            r.workflow_version_id,
            (h.created_at {_DAY} AS metric_date,
            {_metric_select_list({"staff_handoff": "COUNT(*)::bigint"})},
            'USD'::varchar(3) AS currency
        FROM campaign_staff_handoffs h
        JOIN automation_workflow_runs r ON r.id = h.workflow_run_id
        WHERE h.workflow_run_id IS NOT NULL
          AND (h.created_at {_DAY} >= :start_date
          AND (h.created_at {_DAY} <= :end_date
        GROUP BY 1, 2, 3, 4, 5
"""


_ROLLUP_BRANCHES = (
    _RUN_ENROLLMENT_BRANCH,
    _RUN_OUTCOME_BRANCH,
    _SMS_BRANCH,
    _VOICE_BRANCH,
    _USAGE_BRANCH,
    _RESPONSE_BRANCH,
    _HANDOFF_BRANCH,
)

_UNIONED_BRANCHES = "        UNION ALL\n".join(_ROLLUP_BRANCHES)


_INSERT_ROLLUP_SQL = text(
    f"""
    WITH metric_events AS ({_UNIONED_BRANCHES}    ),
    rolled AS (
        SELECT
            institution_id,
            location_id,
            workflow_id,
            workflow_version_id,
            metric_date,
            {_ROLLED_SUMS},
            COALESCE(MAX(currency), 'USD')::varchar(3) AS currency
        FROM metric_events
        GROUP BY 1, 2, 3, 4, 5
    )
    INSERT INTO campaign_metrics_daily (
        institution_id, location_id, workflow_id, workflow_version_id, metric_date,
        {_METRIC_COLUMN_LIST},
        cost_per_booking, cost_per_confirmation, currency, updated_at
    )
    SELECT
        institution_id, location_id, workflow_id, workflow_version_id, metric_date,
        {_METRIC_COLUMN_LIST},
        CASE WHEN booked > 0 THEN total_cost / booked ELSE NULL END AS cost_per_booking,
        CASE WHEN confirmed > 0 THEN total_cost / confirmed ELSE NULL END AS cost_per_confirmation,
        currency,
        NOW()
    FROM rolled
    """
)


async def recompute_window(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, int]:
    """Rebuild campaign metric rows for the inclusive UTC date window."""
    if start_date > end_date:
        raise ValueError(f"start_date ({start_date}) must be <= end_date ({end_date})")

    params = {
        "start_date": start_date,
        "end_date": end_date,
        "null_location_sentinel": NULL_LOCATION_SENTINEL,
    }
    deleted_result = await session.execute(_DELETE_ROLLUP_SQL, params)
    insert_result = await session.execute(_INSERT_ROLLUP_SQL, params)
    deleted = deleted_result.rowcount or 0
    inserted = insert_result.rowcount or 0
    logger.info(
        "Campaign analytics rollup recompute: window=[%s, %s] inserted=%d deleted=%d",
        start_date,
        end_date,
        inserted,
        deleted,
    )
    return {"inserted": inserted, "deleted": deleted}


async def recompute_recent(session: AsyncSession, *, today: date) -> dict[str, int]:
    """Periodic refresh for recent data and late provider/response events."""
    return await recompute_window(
        session, start_date=today - timedelta(days=1), end_date=today
    )


class CampaignAnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def workflow_analytics(
        self,
        workflow: AutomationWorkflow,
        *,
        institution_id: str,
        start_date: date,
        end_date: date,
    ) -> CampaignAnalytics:
        metrics = await self._summed_metrics(
            institution_id=institution_id,
            workflow_id=str(workflow.id),
            start_date=start_date,
            end_date=end_date,
        )
        trend = await self._trend(
            institution_id=institution_id,
            workflow_id=str(workflow.id),
            start_date=start_date,
            end_date=end_date,
        )
        category = campaign_category(workflow)
        return _analytics_from_metrics(
            workflow_id=str(workflow.id),
            workflow_name=workflow.name,
            category=category,
            start_date=start_date,
            end_date=end_date,
            metrics=metrics,
            trend=trend,
        )

    async def campaign_rollups(
        self,
        *,
        institution_id: str,
        start_date: date,
        end_date: date,
        limit: int,
    ) -> list[CampaignAnalytics]:
        sum_columns = [
            func.coalesce(func.sum(getattr(CampaignMetricsDaily, column)), 0).label(
                column
            )
            for column in ROLLUP_METRIC_COLUMNS
        ]
        rows = (
            await self.session.execute(
                select(
                    AutomationWorkflow,
                    *sum_columns,
                    func.max(CampaignMetricsDaily.updated_at).label("rollup_fresh_at"),
                )
                .join(
                    CampaignMetricsDaily,
                    CampaignMetricsDaily.workflow_id == AutomationWorkflow.id,
                )
                .where(
                    CampaignMetricsDaily.institution_id == institution_id,
                    CampaignMetricsDaily.metric_date >= start_date,
                    CampaignMetricsDaily.metric_date <= end_date,
                )
                .group_by(AutomationWorkflow.id)
                .order_by(
                    func.coalesce(func.sum(CampaignMetricsDaily.total_cost), 0).desc()
                )
                .limit(limit)
            )
        ).all()
        results: list[CampaignAnalytics] = []
        for row in rows:
            workflow = row[0]
            values = dict(
                zip(ROLLUP_METRIC_COLUMNS, row[1 : 1 + len(ROLLUP_METRIC_COLUMNS)])
            )
            values["rollup_fresh_at"] = row[-1]
            results.append(
                _analytics_from_metrics(
                    workflow_id=str(workflow.id),
                    workflow_name=workflow.name,
                    category=campaign_category(workflow),
                    start_date=start_date,
                    end_date=end_date,
                    metrics=values,
                    trend=[],
                )
            )
        return results

    async def _summed_metrics(
        self,
        *,
        institution_id: str,
        workflow_id: str,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        sum_columns = [
            func.coalesce(func.sum(getattr(CampaignMetricsDaily, column)), 0).label(
                column
            )
            for column in ROLLUP_METRIC_COLUMNS
        ]
        row = (
            await self.session.execute(
                select(
                    *sum_columns,
                    func.max(CampaignMetricsDaily.updated_at).label("rollup_fresh_at"),
                ).where(
                    CampaignMetricsDaily.institution_id == institution_id,
                    CampaignMetricsDaily.workflow_id == workflow_id,
                    CampaignMetricsDaily.metric_date >= start_date,
                    CampaignMetricsDaily.metric_date <= end_date,
                )
            )
        ).one()
        values = dict(zip(ROLLUP_METRIC_COLUMNS, row[: len(ROLLUP_METRIC_COLUMNS)]))
        values["rollup_fresh_at"] = row[-1]
        return {**_ZERO_METRICS, **values}

    async def _trend(
        self,
        *,
        institution_id: str,
        workflow_id: str,
        start_date: date,
        end_date: date,
    ) -> list[TrendPoint]:
        rows = (
            await self.session.execute(
                select(
                    CampaignMetricsDaily.metric_date,
                    func.coalesce(func.sum(CampaignMetricsDaily.enrollments), 0),
                    func.coalesce(func.sum(CampaignMetricsDaily.sms_sent), 0),
                    func.coalesce(func.sum(CampaignMetricsDaily.email_sent), 0),
                    func.coalesce(func.sum(CampaignMetricsDaily.voice_attempted), 0),
                    func.coalesce(func.sum(CampaignMetricsDaily.sms_replied), 0),
                    func.coalesce(func.sum(CampaignMetricsDaily.confirmed), 0),
                    func.coalesce(func.sum(CampaignMetricsDaily.booked), 0),
                    func.coalesce(func.sum(CampaignMetricsDaily.staff_handoff), 0),
                    func.coalesce(func.sum(CampaignMetricsDaily.total_cost), 0),
                )
                .where(
                    CampaignMetricsDaily.institution_id == institution_id,
                    CampaignMetricsDaily.workflow_id == workflow_id,
                    CampaignMetricsDaily.metric_date >= start_date,
                    CampaignMetricsDaily.metric_date <= end_date,
                )
                .group_by(CampaignMetricsDaily.metric_date)
                .order_by(CampaignMetricsDaily.metric_date)
            )
        ).all()
        return [
            TrendPoint(
                date=row[0],
                enrollments=int(row[1] or 0),
                sends=int((row[2] or 0) + (row[3] or 0) + (row[4] or 0)),
                responses=int(row[5] or 0),
                confirmed=int(row[6] or 0),
                booked=int(row[7] or 0),
                handoffs=int(row[8] or 0),
                total_cost=float(row[9] or Decimal("0")),
            )
            for row in rows
        ]


def campaign_category(workflow: AutomationWorkflow) -> str:
    """Resolve a workflow to the analytics category used for labels."""
    raw_category = (workflow.category or "").lower()
    trigger_type = (workflow.trigger_type or "").lower()
    name = (workflow.name or "").lower()
    if raw_category in {"recall", "callback", "treatment", "reactivation"}:
        return raw_category
    if raw_category == "appointment_ops" or trigger_type == "appointment_offset":
        if "confirm" in name or _definition_has_outcome(
            workflow.definition, "confirmed"
        ):
            return "appointment_confirmation"
        return "appointment_ops"
    if trigger_type == "callback_requested":
        return "callback"
    if trigger_type == "recall_scan":
        return "recall"
    return raw_category or "default"


def outcome_definitions(category: str) -> tuple[OutcomeDefinition, ...]:
    return _OUTCOME_DEFINITIONS.get(category) or _OUTCOME_DEFINITIONS["default"]


def resolve_window(
    start_date: date | None,
    end_date: date | None,
    *,
    today: date | None = None,
) -> tuple[date, date]:
    today = today or date.today()
    end = min(end_date or today, today)
    start = start_date or (end - timedelta(days=29))
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    if (end - start).days > 731:
        raise ValueError("date range may not exceed 731 days")
    return start, end


def _analytics_from_metrics(
    *,
    workflow_id: str,
    workflow_name: str,
    category: str,
    start_date: date,
    end_date: date,
    metrics: dict[str, Any],
    trend: list[TrendPoint],
) -> CampaignAnalytics:
    summary = {
        column: int(metrics.get(column) or 0)
        for column in ROLLUP_METRIC_COLUMNS
        if column != "total_cost"
    }
    total_cost = float(metrics.get("total_cost") or Decimal("0"))
    confirmed = summary["confirmed"]
    booked = summary["booked"]
    enrollments = summary["enrollments"]
    channels = [
        ChannelAnalytics(
            channel="sms",
            attempted=summary["sms_sent"],
            delivered=summary["sms_delivered"],
            failed=summary["sms_failed"],
            responded=summary["sms_replied"],
        ),
        ChannelAnalytics(
            channel="voice",
            attempted=summary["voice_attempted"],
            delivered=summary["voice_answered"],
            failed=summary["voice_failed"],
            responded=summary["voice_answered"],
        ),
        ChannelAnalytics(
            channel="email",
            attempted=summary["email_sent"],
            delivered=summary["email_delivered"],
            failed=summary["email_bounced"],
            responded=summary["email_clicked"],
        ),
    ]
    outcomes = [
        OutcomeAnalytics(
            key=definition.key,
            label=definition.label,
            group=definition.group,
            count=summary.get(definition.key, 0),
            rate=_rate(summary.get(definition.key, 0), enrollments),
            description=definition.description,
        )
        for definition in outcome_definitions(category)
    ]
    return CampaignAnalytics(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        category=category,
        start_date=start_date,
        end_date=end_date,
        summary=summary,
        channels=channels,
        outcomes=outcomes,
        trend=trend,
        cost=CostSummary(
            currency="USD",
            total_cost=total_cost,
            cost_per_booking=(total_cost / booked if booked else None),
            cost_per_confirmation=(total_cost / confirmed if confirmed else None),
        ),
        generated_at=datetime.now(timezone.utc),
        rollup_fresh_at=metrics.get("rollup_fresh_at"),
    )


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _definition_has_outcome(definition: dict[str, Any] | None, outcome: str) -> bool:
    if not definition:
        return False
    for node in definition.get("nodes") or []:
        if isinstance(node, dict) and node.get("outcome") == outcome:
            return True
    return False
