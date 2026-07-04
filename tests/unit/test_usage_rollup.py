"""Static-shape + contract checks for the usage rollup (Plan 11 M-2).

A real Postgres round-trip belongs in an integration test; these unit checks pin
the SQL contract (every metric column covered, sentinel wired through both paths)
and the campaign-tag plumbing on the metering service.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import IntegrityError

from src.app.models.usage_cost_rollup import NULL_LOCATION_SENTINEL, UsageCostRollup
from src.app.services import usage_rollup
from src.app.services.usage_metering_service import UsageMeteringService


# ── Rollup SQL contract ──────────────────────────────────────────────────────


def test_null_location_sentinel_matches_model_constant():
    assert usage_rollup._NULL_LOCATION_SENTINEL == NULL_LOCATION_SENTINEL


def test_upsert_sql_covers_every_metric_column():
    sql = str(usage_rollup._UPSERT_ROLLUP_SQL.text)
    metric_columns = {
        "event_count",
        "total_segments",
        "total_dials",
        "total_emails",
        "total_minutes",
        "total_cost_amount",
        "currency",
        "updated_at",
    }
    for column in metric_columns:
        assert column in sql, f"Rollup SQL missing column {column!r}"

    model_columns = {c.name for c in UsageCostRollup.__table__.columns}
    pk_columns = {"institution_id", "location_id", "usage_date", "channel", "direction"}
    assert metric_columns | pk_columns == model_columns, (
        "Model has columns not covered by the rollup SQL: "
        f"{model_columns - (metric_columns | pk_columns)}"
    )


def test_delete_empty_sql_uses_same_sentinel():
    sql = str(usage_rollup._DELETE_EMPTY_SQL.text)
    assert ":null_location_sentinel" in sql
    assert NULL_LOCATION_SENTINEL not in sql


def test_recompute_window_rejects_inverted_window():
    async def go():
        session = AsyncMock()
        try:
            await usage_rollup.recompute_window(
                session, start_date=date(2026, 6, 10), end_date=date(2026, 6, 1)
            )
        except ValueError as exc:
            return str(exc)
        return None

    err = asyncio.run(go())
    assert err is not None and "start_date" in err


def test_recompute_recent_uses_today_and_yesterday():
    calls = {}

    async def fake_recompute_window(session, *, start_date, end_date):
        calls["start"] = start_date
        calls["end"] = end_date
        return {"upserted": 0, "deleted": 0}

    orig = usage_rollup.recompute_window
    usage_rollup.recompute_window = fake_recompute_window
    try:
        asyncio.run(usage_rollup.recompute_recent(AsyncMock(), today=date(2026, 6, 15)))
    finally:
        usage_rollup.recompute_window = orig

    assert calls["end"] == date(2026, 6, 15)
    assert calls["start"] == date(2026, 6, 14)


# ── Campaign-tag plumbing on the metering service (M-4) ──────────────────────


class _NestedCM:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def _make_session(*, integrity_error: bool = False) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = (
        AsyncMock(side_effect=IntegrityError("s", {}, Exception("dup")))
        if integrity_error
        else AsyncMock()
    )
    session.begin_nested = MagicMock(return_value=_NestedCM())
    return session


def test_record_persists_campaign_tags():
    session = _make_session()
    svc = UsageMeteringService(session)
    event = asyncio.run(
        svc.record(
            institution_id="inst-1",
            channel="voice",
            direction="outbound",
            provider="retell",
            workflow_run_id="run-9",
            workflow_id="wf-9",
            minutes=Decimal("1.5"),
            dials=1,
            idempotency_key="retell:call-1",
        )
    )
    assert event is not None
    assert event.workflow_run_id == "run-9"
    assert event.workflow_id == "wf-9"
    assert event.channel == "voice"
    assert event.minutes == Decimal("1.5")
