"""Recompute the daily dashboard rollup.

Takes a window ``[start_date, end_date]`` and rebuilds the
``call_metrics_daily`` rows in that window from ``calls`` via a single
UPSERT-from-SELECT. Idempotent: re-running the same window produces
identical rows. ``calls.location_id IS NULL`` is mapped to the all-zero
``NULL_LOCATION_SENTINEL`` so the rollup PK can be ``NOT NULL``.

Two callers:

  * **Backfill** — one-shot, on schema changes / first deploy. Window
    spans every date that has any rows in ``calls``.
  * **Periodic** — Celery beat, every ~5 minutes. Window is
    ``today`` and ``today - 1`` so late-arriving rows (post-call
    analytics, retroactive callback resolutions) settle within minutes.

Both callers run as the migration role (the ``DATABASE_ADMIN_URL``
identity), not the runtime ``nexhealth_app`` role — RLS would otherwise
limit the recompute to a single tenant per call. The WITH CHECK clause
on the rollup's RLS policy permits inserts from the ``celery`` and
``audit`` system contexts so a future scheduled-Fargate refresh path
can run as the runtime role and still write its own tenant's rows.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


_NULL_LOCATION_SENTINEL = "00000000-0000-0000-0000-000000000000"


# Single-statement UPSERT: rebuild every (institution, location, day) row
# in the window from scratch. PostgreSQL evaluates the SELECT once, builds
# the per-status JSONB via a subquery using FILTER, and the
# ON CONFLICT DO UPDATE replaces stale rows in place. Rows in
# ``call_metrics_daily`` for tuples with no calls in the window are NOT
# touched — see ``_delete_empty_rows_in_window`` below for the cleanup.
_UPSERT_ROLLUP_SQL = text(
    """
    WITH base AS (
        SELECT
            calls.institution_id,
            COALESCE(calls.location_id, CAST(:null_location_sentinel AS uuid)) AS location_id,
            calls.call_date,
            COUNT(*)                                                AS total_calls,
            COUNT(*) FILTER (WHERE calls.is_new_patient)            AS new_patient_calls,
            COUNT(*) FILTER (WHERE calls.is_complaint)              AS complaint_calls,
            COUNT(*) FILTER (WHERE calls.is_insurance_billing)      AS insurance_billing_calls,
            COALESCE(SUM(calls.call_duration_seconds), 0)::bigint   AS total_duration_seconds
        FROM calls
        WHERE calls.call_date >= :start_date AND calls.call_date <= :end_date
        GROUP BY 1, 2, 3
    ),
    tags AS (
        SELECT
            per_status.institution_id,
            per_status.location_id,
            per_status.call_date,
            jsonb_object_agg(per_status.call_status, per_status.cnt) AS tag_counts
        FROM (
            SELECT
                calls.institution_id,
                COALESCE(calls.location_id, CAST(:null_location_sentinel AS uuid)) AS location_id,
                calls.call_date,
                calls.call_status,
                COUNT(*) AS cnt
            FROM calls
            WHERE calls.call_date >= :start_date
              AND calls.call_date <= :end_date
              AND calls.call_status IS NOT NULL
            GROUP BY 1, 2, 3, 4
        ) per_status
        GROUP BY 1, 2, 3
    )
    INSERT INTO call_metrics_daily AS target (
        institution_id,
        location_id,
        call_date,
        total_calls,
        new_patient_calls,
        complaint_calls,
        insurance_billing_calls,
        total_duration_seconds,
        tag_counts,
        updated_at
    )
    SELECT
        base.institution_id,
        base.location_id,
        base.call_date,
        base.total_calls,
        base.new_patient_calls,
        base.complaint_calls,
        base.insurance_billing_calls,
        base.total_duration_seconds,
        COALESCE(tags.tag_counts, '{}'::jsonb),
        NOW()
    FROM base
    LEFT JOIN tags
        ON tags.institution_id = base.institution_id
       AND tags.location_id   = base.location_id
       AND tags.call_date     = base.call_date
    ON CONFLICT (institution_id, location_id, call_date) DO UPDATE SET
        total_calls             = EXCLUDED.total_calls,
        new_patient_calls       = EXCLUDED.new_patient_calls,
        complaint_calls         = EXCLUDED.complaint_calls,
        insurance_billing_calls = EXCLUDED.insurance_billing_calls,
        total_duration_seconds  = EXCLUDED.total_duration_seconds,
        tag_counts              = EXCLUDED.tag_counts,
        updated_at              = EXCLUDED.updated_at
    """
)


# Drop rollup rows whose source data was deleted from ``calls``. Without
# this a row that used to have N calls and now has 0 (because the calls
# were removed) would stay forever at its last computed N.
_DELETE_EMPTY_SQL = text(
    """
    DELETE FROM call_metrics_daily
    WHERE call_date >= :start_date
      AND call_date <= :end_date
      AND NOT EXISTS (
          SELECT 1 FROM calls
          WHERE calls.institution_id = call_metrics_daily.institution_id
            AND COALESCE(calls.location_id, CAST(:null_location_sentinel AS uuid)) = call_metrics_daily.location_id
            AND calls.call_date = call_metrics_daily.call_date
      )
    """
)


async def recompute_window(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, int]:
    """Rebuild ``call_metrics_daily`` for the inclusive window.

    Returns a small summary dict so callers (cron jobs, ad-hoc
    backfills) can log what changed. The session is the caller's —
    we don't commit; let the caller decide transaction boundaries.
    """
    if start_date > end_date:
        raise ValueError(
            f"start_date ({start_date}) must be <= end_date ({end_date})"
        )

    params: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "null_location_sentinel": _NULL_LOCATION_SENTINEL,
    }

    upsert_result = await session.execute(_UPSERT_ROLLUP_SQL, params)
    upserted = upsert_result.rowcount or 0

    delete_result = await session.execute(_DELETE_EMPTY_SQL, params)
    deleted = delete_result.rowcount or 0

    logger.info(
        "Dashboard rollup recompute: window=[%s, %s] upserted=%d deleted=%d",
        start_date,
        end_date,
        upserted,
        deleted,
    )
    return {"upserted": upserted, "deleted": deleted}


async def recompute_recent(session: AsyncSession, *, today: date) -> dict[str, int]:
    """Periodic refresh — recompute today and yesterday only.

    Yesterday is included so late-arriving call rows (e.g., webhooks
    delivered after midnight UTC) bump yesterday's totals on the next
    refresh. Anything older than that is treated as immutable.
    """
    return await recompute_window(
        session,
        start_date=today - timedelta(days=1),
        end_date=today,
    )
