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
async def test_get_aggregate_dashboard_success(async_client: AsyncClient):
    """Institution admin receives aggregate summary + clinic comparison rows."""
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

        tag_rows_result = MagicMock()
        tag_rows_result.all.return_value = [
            SimpleNamespace(call_status="appointment_booked", cnt=6),
            SimpleNamespace(call_status="needs_callback", cnt=2),
        ]

        summary_result = _row_result(
            total_calls_today=4,
            total_calls_week=12,
            total_calls_month=20,
            total_calls_all_time=100,
            appointments_booked_month=8,
            new_patients_month=3,
            open_callbacks=2,
            avg_call_duration_seconds=95.0,
        )

        metrics_rows_result = MagicMock()
        metrics_rows_result.all.return_value = [
            SimpleNamespace(
                location_id=loc_1.id,
                calls_today=3,
                calls_this_month=10,
                appointments_booked_month=5,
                new_patients_month=2,
                open_callbacks=1,
                avg_duration=95.5,
            ),
        ]

        mock_session.execute.side_effect = [
            locations_result,
            summary_result,
            tag_rows_result,
            metrics_rows_result,
        ]

        try:
            response = await async_client.get("/api/institution/dashboard/aggregate")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200
    payload = response.json()

    assert payload["summary"]["total_calls_month"] == 20
    assert payload["summary"]["appointments_booked_month"] == 8
    assert payload["summary"]["booking_rate_month"] == 40.0
    assert payload["summary"]["open_callbacks"] == 2

    assert len(payload["tag_distribution"]) == 2
    assert payload["tag_distribution"][0]["tag"] == "appointment_booked"

    assert len(payload["clinic_comparison"]) == 2
    first = payload["clinic_comparison"][0]
    assert first["location_slug"] == "downtown"
    assert first["calls_this_month"] == 10
    assert first["booking_rate_month"] == 50.0

    second = payload["clinic_comparison"][1]
    assert second["location_slug"] == "uptown"
    assert second["calls_this_month"] == 0
    assert second["booking_rate_month"] == 0.0
    assert mock_session.execute.await_count == 4


@pytest.mark.asyncio
async def test_get_dashboard_summary_success_uses_collapsed_volume_query(async_client: AsyncClient):
    """Dashboard summary keeps the same payload while reducing count queries to one DB call."""
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

        summary_result = _row_result(
            today_count=3,
            week_count=9,
            month_count=14,
            all_time_count=44,
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
            summary_result,
            tag_rows_result,
            callback_rows_result,
        ]

        try:
            response = await async_client.get("/api/institution/dashboard/summary")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200
    payload = response.json()

    assert payload["call_volume"] == {
        "today": 3,
        "this_week": 9,
        "this_month": 14,
        "all_time": 44,
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
    assert mock_session.execute.await_count == 3


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

        summary_result = _row_result(
            today_count=1,
            week_count=2,
            month_count=3,
            all_time_count=10,
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
            summary_result,
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
