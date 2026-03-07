"""
Integration tests for super-admin institution management endpoints.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import get_current_admin
from src.app.main import app
from src.app.models.user import User, UserRole


@pytest.mark.asyncio
async def test_create_institution_with_initial_admin(async_client: AsyncClient):
    """SUPER_ADMIN can create an institution and invite the initial institution admin."""
    payload = {
        "name": "ScaleNexus Dental",
        "slug": "scalenexus-dental",
        "email": "owner@example.com",
        "location_limit": 3,
    }

    mock_super_admin = User(
        id="99999999-9999-9999-9999-999999999999",
        email="super@example.com",
        role=UserRole.SUPER_ADMIN.value,
        institution_id=None,
        is_active=True,
    )
    app.dependency_overrides[get_current_admin] = lambda: mock_super_admin

    with patch("src.app.api.routes.admin_institutions.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.admin_institutions.InstitutionService"
    ) as MockInstitutionService, patch(
        "src.app.api.routes.admin_institutions.SupabaseService"
    ) as MockSupabaseService:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        # Existing user check query result
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = query_result

        mock_service = AsyncMock()
        mock_service.get_by_slug.return_value = None
        mock_service.create.return_value = SimpleNamespace(
            id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            name=payload["name"],
            slug=payload["slug"],
            is_active=True,
            location_limit=payload["location_limit"],
            nexhealth_api_key_encrypted=None,
        )
        MockInstitutionService.return_value = mock_service

        mock_supabase = MagicMock()
        mock_response = MagicMock()
        mock_response.user.id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        mock_supabase.invite_user.return_value = mock_response
        MockSupabaseService.return_value = mock_supabase

        try:
            response = await async_client.post("/admin/institutions", json=payload)
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == payload["name"]
    assert data["slug"] == payload["slug"]
    assert data["location_limit"] == payload["location_limit"]
    assert data["user"]["email"] == payload["email"]
    assert data["user"]["role"] == UserRole.INSTITUTION_ADMIN.value

    mock_supabase.invite_user.assert_called_once()
    invite_kwargs = mock_supabase.invite_user.call_args.kwargs
    assert invite_kwargs["email"] == payload["email"]
    assert invite_kwargs["institution_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert invite_kwargs["role"] == UserRole.INSTITUTION_ADMIN.value
