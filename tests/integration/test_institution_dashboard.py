"""
Integration tests for institution dashboard endpoints.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import get_current_active_user, get_current_institution_admin
from src.app.main import app
from src.app.models.user import User, UserRole


def _row_result(**kwargs):
    result = MagicMock()
    result.one.return_value = SimpleNamespace(**kwargs)
    return result


@pytest.mark.asyncio
async def test_get_aggregate_dashboard_combines_rollup_and_live(async_client: AsyncClient):
    """Institution admin aggregate dashboard pulls historical metrics from
    ``call_metrics_daily`` (date < today) and overlays today's live counts.

    The query sequence is:

      1. locations
      2. institution-wide rollup summary (date < today)
      3. institution-wide live today + open_callbacks
      4. rollup tag distribution (jsonb_each_text)
      5. live today tag distribution
      6. per-location rollup (date < today, GROUP BY location_id)
      7. per-location live today + open_callbacks (GROUP BY location_id)

    All seven mocks are set up so the totals exactly match the legacy
    test's payload, proving the refactor is response-compatible.
    """
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
    )
    app.dependency_overrides[get_current_institution_admin] = lambda: mock_user

    loc_1 = SimpleNamespace(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        name="Downtown",
        slug="downtown",
        is_active=True,
        retell_agent_id="agent-1",
    )
    loc_2 = SimpleNamespace(
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        name="Uptown",
        slug="uptown",
        is_active=False,
        retell_agent_id="agent-2",
    )

    with patch("src.app.api.routes.dashboard.get_db_session") as mock_get_db:
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        locations_result = MagicMock()
        locations_result.scalars.return_value.all.return_value = [loc_1, loc_2]

        # Rollup summary (date < today): 16 calls this month (today adds 4
        # → 20), 7 appointments booked (today adds 1 → 8), 2 new patients
        # (today adds 1 → 3), 96 calls all-time (today adds 4 → 100).
        rollup_summary_result = _row_result(
            week_total=8,
            month_total=16,
            all_time_total=96,
            new_patients_month=2,
            appointments_booked_month=7,
            all_time_duration=9120,  # 95s avg × 96 calls
        )

        # Live today + open_callbacks.
        live_summary_result = _row_result(
            today_count=4,
            today_appointments_booked=1,
            today_new_patients=1,
            today_duration=380,  # 95s avg × 4 calls
            open_callbacks=2,
        )

        # Rollup tag distribution.
        rollup_tags_result = MagicMock()
        rollup_tags_result.all.return_value = [
            SimpleNamespace(tag="appointment_booked", cnt=5),
            SimpleNamespace(tag="needs_callback", cnt=2),
        ]
        # Live today tag distribution.
        live_tags_result = MagicMock()
        live_tags_result.all.return_value = [
            SimpleNamespace(call_status="appointment_booked", cnt=1),
        ]

        # Per-location rollup (only loc_1 has historical data).
        per_location_rollup_result = MagicMock()
        per_location_rollup_result.all.return_value = [
            SimpleNamespace(
                location_id=loc_1.id,
                total_calls=80,
                calls_this_month=7,
                new_patients_month=2,
                appointments_booked_month=4,
                total_duration_seconds=7600,
            ),
        ]
        # Per-location live today (only loc_1 has activity today).
        per_location_live_result = MagicMock()
        per_location_live_result.all.return_value = [
            SimpleNamespace(
                location_id=loc_1.id,
                calls_today=3,
                today_appointments_booked=1,
                today_new_patients=0,
                today_duration=285,
                open_callbacks=1,
            ),
        ]

        mock_session.execute.side_effect = [
            locations_result,
            rollup_summary_result,
            live_summary_result,
            rollup_tags_result,
            live_tags_result,
            per_location_rollup_result,
            per_location_live_result,
        ]

        try:
            response = await async_client.get("/api/institution/dashboard/aggregate")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200, response.text
    payload = response.json()

    # Aggregate summary: rollup + today.
    assert payload["summary"]["total_calls_today"] == 4
    assert payload["summary"]["total_calls_month"] == 20  # 16 + 4
    assert payload["summary"]["total_calls_all_time"] == 100  # 96 + 4
    assert payload["summary"]["appointments_booked_month"] == 8  # 7 + 1
    assert payload["summary"]["new_patients_month"] == 3  # 2 + 1
    assert payload["summary"]["booking_rate_month"] == 40.0  # 8/20
    assert payload["summary"]["open_callbacks"] == 2

    # Tag distribution combines rollup + live and re-sorts.
    assert len(payload["tag_distribution"]) == 2
    assert payload["tag_distribution"][0]["tag"] == "appointment_booked"
    assert payload["tag_distribution"][0]["count"] == 6  # 5 rollup + 1 today
    assert payload["tag_distribution"][1]["tag"] == "needs_callback"
    assert payload["tag_distribution"][1]["count"] == 2

    # Clinic comparison: loc_1 has data, loc_2 has zeroes.
    assert len(payload["clinic_comparison"]) == 2
    first = payload["clinic_comparison"][0]
    assert first["location_slug"] == "downtown"
    assert first["calls_today"] == 3
    assert first["calls_this_month"] == 10  # 7 rollup + 3 today
    assert first["appointments_booked_month"] == 5  # 4 rollup + 1 today
    assert first["new_patients_month"] == 2  # 2 + 0
    assert first["booking_rate_month"] == 50.0  # 5/10
    assert first["open_callbacks"] == 1

    second = payload["clinic_comparison"][1]
    assert second["location_slug"] == "uptown"
    assert second["calls_this_month"] == 0
    assert second["booking_rate_month"] == 0.0
    assert second["open_callbacks"] == 0

    # 7 queries total — see docstring at the top of this test.
    assert mock_session.execute.await_count == 7


@pytest.mark.asyncio
async def test_get_dashboard_summary_combines_rollup_and_today_live(async_client: AsyncClient):
    """Volume cards stitch rollup totals (date < today) with live today count.

    The dashboard issues a tight live ``COUNT(*)`` for today's calls and
    pulls week/month/all-time SUMs from ``call_metrics_daily``. Today is
    bracketed in [week_start, today] and [month_start, today], so it
    must always be added to the bucketed sums.
    """
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
    )
    app.dependency_overrides[get_current_active_user] = lambda: mock_user

    with patch("src.app.api.routes.dashboard.get_db_session") as mock_get_db:
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        # Today: 3 live calls.
        today_count_result = MagicMock()
        today_count_result.scalar_one.return_value = 3

        # Rollup (date < today): 6 this week so far, 11 this month, 41 all-time.
        rollup_row_result = _row_result(
            week_total=6,
            month_total=11,
            all_time_total=41,
        )

        tag_rows_result = MagicMock()
        tag_rows_result.all.return_value = [
            SimpleNamespace(call_status="appointment_booked", cnt=5),
            SimpleNamespace(call_status="needs_callback", cnt=2),
        ]

        callback_rows_result = MagicMock()
        callback_rows_result.all.return_value = [
            (
                SimpleNamespace(
                    id="call-1",
                    call_date=date(2026, 4, 20),
                    call_time="09:15:00",
                    call_duration_seconds=120,
                    summary="Follow up about insurance",
                    next_action="Call back this afternoon",
                ),
                SimpleNamespace(full_name="Sarah Loomer"),
            ),
        ]

        mock_session.execute.side_effect = [
            today_count_result,
            rollup_row_result,
            tag_rows_result,
            callback_rows_result,
        ]

        try:
            response = await async_client.get("/api/institution/dashboard/summary")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200
    payload = response.json()

    # Each bucketed sum = rollup + today.
    assert payload["call_volume"] == {
        "today": 3,
        "this_week": 9,    # 6 (rollup, date<today) + 3 (today)
        "this_month": 14,  # 11 + 3
        "all_time": 44,    # 41 + 3
    }
    assert payload["tag_counts"][0]["tag"] == "appointment_booked"
    assert payload["callback_queue"] == [
        {
            "call_id": "call-1",
            "contact_name": "Sarah Loomer",
            "call_date": "2026-04-20",
            "call_time": "09:15:00",
            "call_duration_seconds": 120,
            "summary": "Follow up about insurance",
            "next_action": "Call back this afternoon",
        }
    ]
    assert mock_session.execute.await_count == 4


@pytest.mark.asyncio
async def test_summary_scopes_staff_by_call_location_id_not_agent_used(
    async_client: AsyncClient,
):
    """Staff dashboard must scope by Call.location_id (authoritative) and not
    by Call.agent_used (stale Retell metadata).

    Regression: a call with the right location_id but a stale or unknown
    agent_used was being dropped from the dashboard while still appearing
    in /api/institution/calls — operationally dangerous because callback
    queue items would silently disappear.

    Locations without a retell_agent_id mapping must also still produce a
    dashboard (under the old code they 403'd with "Invalid location
    scope"), since location_id no longer depends on the agent mapping.
    """
    location_id = "22222222-2222-4222-8222-222222222222"
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="staff@clinic.com",
        role=UserRole.STAFF.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        location_id=location_id,
        is_active=True,
    )
    app.dependency_overrides[get_current_active_user] = lambda: mock_user

    captured_queries: list[str] = []

    with patch("src.app.api.routes.dashboard.get_db_session") as mock_get_db:
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        # Location has NO retell_agent_id — under the old code this 403'd.
        location_result = MagicMock()
        location_result.scalar_one_or_none.return_value = SimpleNamespace(
            id=location_id,
            name="Main",
            slug="main",
            is_active=True,
            retell_agent_id=None,
            institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )

        today_count_result = MagicMock()
        today_count_result.scalar_one.return_value = 1
        rollup_row_result = _row_result(
            week_total=1,
            month_total=2,
            all_time_total=9,
        )
        tag_rows_result = MagicMock()
        tag_rows_result.all.return_value = []
        callback_rows_result = MagicMock()
        callback_rows_result.all.return_value = []

        async def capture(stmt, *args, **kwargs):
            try:
                compiled = str(
                    stmt.compile(compile_kwargs={"literal_binds": False})
                )
                captured_queries.append(compiled)
            except Exception:
                pass
            return mock_session.execute.side_effect_results.pop(0)

        mock_session.execute.side_effect_results = [
            location_result,
            today_count_result,
            rollup_row_result,
            tag_rows_result,
            callback_rows_result,
        ]
        mock_session.execute.side_effect = capture

        try:
            response = await async_client.get("/api/institution/dashboard/summary")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200, response.text

    # Three filtered queries (volume, tags, callback queue) must scope by
    # calls.location_id — the authoritative scope — not calls.agent_used.
    # Look at the WHERE clause only (agent_used legitimately appears in the
    # callback-queue SELECT list because it joins Call.* + Contact.*).
    filtered_queries = [q for q in captured_queries if "FROM calls" in q]
    assert len(filtered_queries) >= 3, captured_queries
    for q in filtered_queries:
        where_clause = q.split("WHERE", 1)[1] if "WHERE" in q else ""
        assert "calls.location_id" in where_clause, (
            f"Dashboard query WHERE did not scope by Call.location_id:\n{q}"
        )
        assert "calls.agent_used" not in where_clause, (
            f"Dashboard query WHERE still scopes by stale Call.agent_used:\n{q}"
        )


@pytest.mark.asyncio
async def test_summary_rejects_unknown_location_slug(async_client: AsyncClient):
    """Institution admin gets 404 when requesting drill-down for missing location."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
    )
    app.dependency_overrides[get_current_active_user] = lambda: mock_user

    with patch("src.app.api.routes.dashboard.get_db_session") as mock_get_db:
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        missing_location_result = MagicMock()
        missing_location_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = missing_location_result

        try:
            response = await async_client.get("/api/institution/dashboard/summary?location_slug=missing")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 404
    assert response.json()["detail"] == "Location not found"
