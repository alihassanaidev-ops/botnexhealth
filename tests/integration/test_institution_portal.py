"""
Integration tests for institution portal endpoints.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import get_current_institution_or_location_user
from src.app.main import app
from src.app.models.user import User, UserRole


@pytest.mark.asyncio
async def test_get_my_institution_context_success(async_client: AsyncClient):
    """Institution users can read their non-sensitive institution context."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
    )

    app.dependency_overrides[get_current_institution_or_location_user] = lambda: mock_user

    with patch("src.app.api.routes.institution_portal.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.institution_portal.InstitutionService"
    ) as MockInstitutionService:
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        mock_service = AsyncMock()
        mock_service.get_by_id.return_value = SimpleNamespace(
            id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            name="Lawendy Dental Group",
            slug="lawendy-group",
        )
        MockInstitutionService.return_value = mock_service

        try:
            response = await async_client.get("/institution/me")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert data["name"] == "Lawendy Dental Group"
    assert data["slug"] == "lawendy-group"
    assert data["role"] == UserRole.INSTITUTION_ADMIN.value
    # Non-sensitive response contract: no integration booleans/secrets
    assert "has_nexhealth_key" not in data
    assert "has_retell_secret" not in data


@pytest.mark.asyncio
async def test_get_my_institution_context_requires_institution(async_client: AsyncClient):
    """Users without institution assignment are rejected."""
    mock_user = User(
        id="22222222-2222-2222-2222-222222222222",
        email="orphan@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id=None,
        is_active=True,
    )

    app.dependency_overrides[get_current_institution_or_location_user] = lambda: mock_user
    try:
        response = await async_client.get("/institution/me")
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 400
    assert response.json()["detail"] == "User is not associated with an institution"
