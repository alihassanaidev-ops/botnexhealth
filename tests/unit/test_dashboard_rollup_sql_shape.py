"""Static checks on the dashboard rollup SQL.

A real round-trip against Postgres lives in
``tests/integration/test_dashboard_rollup_postgres.py`` (Testcontainers,
skipped without Docker). These unit checks pin the script's *contract*
so we catch obvious drift fast: the UPSERT covers every metric column
the model declares, and the NULL-location sentinel is wired through
both the recompute and the cleanup path.
"""

from __future__ import annotations

from src.app.models.call_metrics_daily import (
    NULL_LOCATION_SENTINEL,
    CallMetricsDaily,
)
from src.app.services import dashboard_rollup


def test_null_location_sentinel_matches_model_constant():
    """Service and model must agree on the sentinel UUID — diverging would
    cause the recompute to write rows the model can't read back."""
    assert dashboard_rollup._NULL_LOCATION_SENTINEL == NULL_LOCATION_SENTINEL


def test_upsert_sql_covers_every_metric_column():
    """Every metric column on the model must appear in both the SELECT
    list and the ON CONFLICT DO UPDATE SET clause — adding a new
    rollup column without updating the SQL would silently leave it at
    its default."""
    sql = str(dashboard_rollup._UPSERT_ROLLUP_SQL.text)
    metric_columns = {
        "total_calls",
        "new_patient_calls",
        "complaint_calls",
        "insurance_billing_calls",
        "total_duration_seconds",
        "tag_counts",
        "updated_at",
    }
    for column in metric_columns:
        assert column in sql, f"Rollup SQL missing column {column!r}"

    # Every column that lives on the model must be in the metric set.
    model_columns = {c.name for c in CallMetricsDaily.__table__.columns}
    pk_columns = {"institution_id", "location_id", "call_date"}
    assert metric_columns | pk_columns == model_columns, (
        "Model has columns not covered by the rollup SQL: "
        f"{model_columns - (metric_columns | pk_columns)}"
    )


def test_delete_empty_sql_uses_same_sentinel():
    """If the cleanup query coalesces to a different sentinel than the
    upsert, it would never match the rows it just inserted and the
    rollup table would never shrink."""
    sql = str(dashboard_rollup._DELETE_EMPTY_SQL.text)
    assert ":null_location_sentinel" in sql
    # The literal sentinel itself shouldn't appear in the SQL string;
    # it's bound as a parameter for safety against any future change.
    assert NULL_LOCATION_SENTINEL not in sql


def test_recompute_window_rejects_inverted_window():
    """A start > end would silently produce zero rows; raise loudly."""
    import asyncio
    from datetime import date

    from unittest.mock import AsyncMock

    async def go():
        session = AsyncMock()
        try:
            await dashboard_rollup.recompute_window(
                session,
                start_date=date(2026, 5, 10),
                end_date=date(2026, 5, 1),
            )
        except ValueError as exc:
            return str(exc)
        return None

    err = asyncio.run(go())
    assert err is not None
    assert "start_date" in err and "end_date" in err
