"""
Integration tests for institution dashboard endpoints.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import get_current_active_user, get_current_institution_admin
from src.app.main import app
from src.app.models.user import User, UserRole


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

        def scalar_result(value: int):
            result = MagicMock()
            result.scalar_one.return_value = value
            return result

        tag_rows_result = MagicMock()
        tag_rows_result.all.return_value = [
            SimpleNamespace(call_status="appointment_booked", cnt=6),
            SimpleNamespace(call_status="needs_callback", cnt=2),
        ]

        metrics_rows_result = MagicMock()
        metrics_rows_result.all.return_value = [
            SimpleNamespace(
                agent_used="agent-1",
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
            scalar_result(4),   # total_calls_today
            scalar_result(12),  # total_calls_week
            scalar_result(20),  # total_calls_month
            scalar_result(100), # total_calls_all_time
            scalar_result(8),   # appointments_booked_month
            scalar_result(3),   # new_patients_month
            scalar_result(2),   # open_callbacks
            scalar_result(95),  # avg_call_duration_seconds
            tag_rows_result,
            metrics_rows_result,
        ]

        try:
            response = await async_client.get("/institution/dashboard/aggregate")
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
            response = await async_client.get("/institution/dashboard/summary?location_slug=missing")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 404
    assert response.json()["detail"] == "Location not found"
