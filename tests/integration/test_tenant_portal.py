"""
Integration tests for Tenant Portal.
"""

from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient
from src.app.models.user import User, UserRole
from src.app.models.tenant import Tenant

@pytest.mark.asyncio
async def test_get_my_tenant_config_success(async_client: AsyncClient):
    """Test successful retrieval of tenant config for a tenant user."""
    
    # Mock tenant
    mock_tenant = Tenant(
        id="tenant-123",
        name="Test Clinic",
        slug="test-clinic",
        is_active=True,
        nexhealth_api_key_encrypted="encrypted_key", # Should result in has_nexhealth_key=True
        ghl_api_key_encrypted=None,                  # Should result in has_ghl_key=False
    )

    # Mock user
    mock_user = User(
        id="user-123",
        email="test@clinic.com",
        role=UserRole.TENANT,
        tenant_id="tenant-123",
        is_active=True
    )

    # Mock dependencies and DB stuff
    with patch("src.app.api.routes.tenant_portal.get_current_active_user") as mock_get_user, \
         patch("src.app.api.routes.tenant_portal.get_db_session") as mock_get_db, \
         patch("src.app.api.routes.tenant_portal.TenantService") as MockTenantService:
        
        # Setup Auth Mock
        from src.app.main import app
        from src.app.api.deps import get_current_active_user
        app.dependency_overrides[get_current_active_user] = lambda: mock_user

        # Setup DB Mock (context manager)
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session
        
        # Setup Service Mock
        mock_service_instance = MockTenantService.return_value
        mock_service_instance.get_by_id = AsyncMock(return_value=mock_tenant)
        
        try:
            response = await async_client.get("/api/v1/tenant/me")
            
            assert response.status_code == 200
            data = response.json()
            
            assert data["slug"] == "test-clinic"
            assert data["has_nexhealth_key"] is True
            assert data["has_ghl_key"] is False
            assert "nexhealth_api_key" not in data # Secrets should not be exposed
            assert data["user"]["email"] == "test@clinic.com"
            
        finally:
            app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_my_tenant_config_no_tenant_id(async_client: AsyncClient):
    """Test that users without tenant_id (e.g. Admins) cannot access."""
    
    mock_user = User(
        id="admin-123",
        email="admin@platform.com",
        role=UserRole.ADMIN,
        tenant_id=None,
        is_active=True
    )
    
    from src.app.main import app
    from src.app.api.deps import get_current_active_user
    app.dependency_overrides[get_current_active_user] = lambda: mock_user
    
    try:
        response = await async_client.get("/api/v1/tenant/me")
        assert response.status_code == 400
        assert response.json()["detail"] == "User is not associated with a tenant"
    finally:
        app.dependency_overrides = {}
