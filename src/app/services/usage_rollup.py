"""Recompute the daily usage & cost rollup (Plan 11 M-2).

Takes a window ``[start_date, end_date]`` and rebuilds ``usage_cost_rollups``
rows in that window from ``usage_events`` via a single UPSERT-from-SELECT.
Idempotent: re-running the same window produces identical rows. Mirrors
``dashboard_rollup`` — ``usage_events.location_id IS NULL`` maps to the all-zero
``NULL_LOCATION_SENTINEL`` so the rollup PK stays NOT NULL.

Two callers:
  * **Backfill** — one-shot, over every date present in ``usage_events``.
  * **Periodic** — Celery beat; window is ``today`` and ``today - 1`` so
    late-arriving provider webhooks (cost/segment updates) settle within minutes.

Runs as the migration/admin role (RLS would otherwise scope the recompute to a
single tenant per call), same as the dashboard rollup.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_NULL_LOCATION_SENTINEL = "00000000-0000-0000-0000-000000000000"


# Rebuild every (institution, location, day, channel, direction) row in the
# window from ``usage_events``. usage_date is derived from occurred_at in UTC.
_UPSERT_ROLLUP_SQL = text(
    """
    WITH base AS (
        SELECT
            usage_events.institution_id,
            COALESCE(usage_events.location_id, CAST(:null_location_sentinel AS uuid)) AS location_id,
            (usage_events.occurred_at AT TIME ZONE 'UTC')::date AS usage_date,
            usage_events.channel,
            usage_events.direction,
            COUNT(*)                                          AS event_count,
            COALESCE(SUM(usage_events.segments), 0)::bigint   AS total_segments,
            COALESCE(SUM(usage_events.dials), 0)::bigint      AS total_dials,
            COALESCE(SUM(usage_events.emails), 0)::bigint     AS total_emails,
            COALESCE(SUM(usage_events.minutes), 0)            AS total_minutes,
            COALESCE(SUM(usage_events.cost_amount), 0)        AS total_cost_amount,
            COALESCE(MAX(usage_events.currency), 'USD')       AS currency
        FROM usage_events
        WHERE (usage_events.occurred_at AT TIME ZONE 'UTC')::date >= :start_date
          AND (usage_events.occurred_at AT TIME ZONE 'UTC')::date <= :end_date
        GROUP BY 1, 2, 3, 4, 5
    )
    INSERT INTO usage_cost_rollups AS target (
        institution_id, location_id, usage_date, channel, direction,
        event_count, total_segments, total_dials, total_emails,
        total_minutes, total_cost_amount, currency, updated_at
    )
    SELECT
        base.institution_id, base.location_id, base.usage_date, base.channel, base.direction,
        base.event_count, base.total_segments, base.total_dials, base.total_emails,
        base.total_minutes, base.total_cost_amount, base.currency, NOW()
    FROM base
    ON CONFLICT (institution_id, location_id, usage_date, channel, direction) DO UPDATE SET
        event_count       = EXCLUDED.event_count,
        total_segments    = EXCLUDED.total_segments,
        total_dials       = EXCLUDED.total_dials,
        total_emails      = EXCLUDED.total_emails,
        total_minutes     = EXCLUDED.total_minutes,
        total_cost_amount = EXCLUDED.total_cost_amount,
        currency          = EXCLUDED.currency,
        updated_at        = EXCLUDED.updated_at
    """
)


# Drop rollup rows whose source events were deleted, so a tuple that used to have
# events and now has none doesn't linger at its last computed value.
_DELETE_EMPTY_SQL = text(
    """
    DELETE FROM usage_cost_rollups
    WHERE usage_date >= :start_date
      AND usage_date <= :end_date
      AND NOT EXISTS (
          SELECT 1 FROM usage_events
          WHERE usage_events.institution_id = usage_cost_rollups.institution_id
            AND COALESCE(usage_events.location_id, CAST(:null_location_sentinel AS uuid)) = usage_cost_rollups.location_id
            AND (usage_events.occurred_at AT TIME ZONE 'UTC')::date = usage_cost_rollups.usage_date
            AND usage_events.channel = usage_cost_rollups.channel
            AND usage_events.direction = usage_cost_rollups.direction
      )
    """
)


async def recompute_window(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, int]:
    """Rebuild ``usage_cost_rollups`` for the inclusive window.

    Returns a summary dict. The session is the caller's — we don't commit.
    """
    if start_date > end_date:
        raise ValueError(f"start_date ({start_date}) must be <= end_date ({end_date})")

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
        "Usage rollup recompute: window=[%s, %s] upserted=%d deleted=%d",
        start_date, end_date, upserted, deleted,
    )
    return {"upserted": upserted, "deleted": deleted}


async def recompute_recent(session: AsyncSession, *, today: date) -> dict[str, int]:
    """Periodic refresh — recompute today and yesterday only.

    Yesterday is included so late provider cost/segment webhooks delivered after
    midnight UTC settle on the next refresh.
    """
    return await recompute_window(
        session, start_date=today - timedelta(days=1), end_date=today
    )
