"""
Integration tests for Admin Authentication.

Note: The login endpoint requires database access. These tests verify route
accessibility and correct error handling without a database.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_users_me_requires_auth(async_client: AsyncClient):
    """Test that /auth/users/me requires authentication (no DB needed)."""
    response = await async_client.get("/auth/users/me")
    assert response.status_code == 401
    data = response.json()
    assert data["detail"] == "Not authenticated"


@pytest.mark.asyncio
async def test_token_endpoint_validation(async_client: AsyncClient):
    """Test that /auth/token returns 422 with missing form data."""
    # Send completely empty body - should fail validation before hitting DB
    response = await async_client.post("/auth/token")
    assert response.status_code == 422  # Validation error
