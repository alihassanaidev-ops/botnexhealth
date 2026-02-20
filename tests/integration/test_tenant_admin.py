"""
Integration tests for Tenant Admin creation.
"""

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
from src.app.models.user import User, UserRole
from src.app.api.deps import get_current_admin
from src.app.main import app

# Mock data
TENANT_SLUG = "test-tenant-admin"
USER_EMAIL = "newuser@example.com"
USER_EMAIL = "newuser@example.com"
# USER_PASS = "securePass123" # Removed

@pytest.mark.asyncio
async def test_create_tenant_with_user(async_client: AsyncClient):
    """Test creating a tenant with an initial user."""
    
    # Payload
    payload = {
        "name": "Test Tenant With User",
        "slug": TENANT_SLUG,
        "email": USER_EMAIL
    }

    # Mock Admin User for Auth Dependency
    mock_admin_user = AsyncMock(spec=User)
    mock_admin_user.role = UserRole.ADMIN.value
    mock_admin_user.is_active = True

    # Override dependency
    app.dependency_overrides[get_current_admin] = lambda: mock_admin_user
    with patch("src.app.api.routes.tenants.get_db_session") as mock_db:
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__.return_value = mock_session
        
        # Configure session.add to be synchronous and set ID
        from unittest.mock import MagicMock
        def side_effect_add(obj):
             obj.id = "user-generated-id-123"

        mock_session.add = MagicMock(side_effect=side_effect_add)
        
        # Mock TenantService
        with patch("src.app.api.routes.tenants.TenantService") as MockService:
            mock_service_instance = AsyncMock()
            MockService.return_value = mock_service_instance
            
            # Setup return values
            mock_service_instance.get_by_slug.return_value = None # No conflict
            
            mock_tenant = AsyncMock()
            mock_tenant.id = "tenant-123"
            mock_tenant.name = payload["name"]
            mock_tenant.slug = payload["slug"]
            mock_tenant.is_active = True
            # Mock encrypted fields as None
            mock_tenant.nexhealth_api_key_encrypted = None
            mock_tenant.ghl_api_key_encrypted = None
            mock_tenant.retell_api_secret_encrypted = None
            mock_tenant.sikka_app_id_encrypted = None
            mock_tenant.sikka_app_secret_encrypted = None
            
            # Other fields
            mock_tenant.ghl_custom_fields = None

            mock_service_instance.create.return_value = mock_tenant
            
            # Mock User check query
            # session.execute is an async call that returns a Result object
            # The Result object itself is SYNCHRONOUS, so we use MagicMock, not AsyncMock
            from unittest.mock import MagicMock
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            
            # When session.execute is awaited, it should return this synchronous mock_result
            mock_session.execute.return_value = mock_result
            
            # When session.execute is awaited, it should return this synchronous mock_result
            mock_session.execute.return_value = mock_result
            
            # Mock SupabaseService
            with patch("src.app.api.routes.tenants.SupabaseService") as MockSupabaseService:
                mock_supabase_instance = MockSupabaseService.return_value
                # Mock response structure: object with response.user.id
                from unittest.mock import MagicMock
                mock_response = MagicMock()
                mock_response.user.id = "supabase-user-id"
                mock_supabase_instance.invite_user.return_value = mock_response
                
                # Act
                response = await async_client.post("/admin/tenants", json=payload)
                
                # Assert
                # Verify Supabase invite was called
                mock_supabase_instance.invite_user.assert_called_once()
                call_args = mock_supabase_instance.invite_user.call_args
                assert call_args.kwargs['email'] == USER_EMAIL
                assert call_args.kwargs['tenant_id'] == "tenant-123"
                assert call_args.kwargs['role'] == "TENANT"
            
            # Assert
            assert response.status_code == 201
            data = response.json()
            
            assert data["slug"] == TENANT_SLUG
            assert data["name"] == "Test Tenant With User"
            
            # Verify User fields
            assert "user" in data
            assert data["user"]["email"] == USER_EMAIL
            assert data["user"]["role"] == "TENANT"
            assert data["user"]["is_active"] is True
            
            # Verify User was added to session
            # We check if session.add was called with a User object having correct role
            assert mock_session.add.called
            args, _ = mock_session.add.call_args
            user_arg = args[0]
            assert user_arg.email == USER_EMAIL
            assert user_arg.role == UserRole.TENANT.value
            assert user_arg.tenant_id == "tenant-123"

    # Clean up override
    app.dependency_overrides = {}
