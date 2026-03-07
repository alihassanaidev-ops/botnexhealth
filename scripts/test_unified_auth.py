import asyncio
import sys
import os

# This is a manual script, not a pytest test module.
__test__ = False

# Add project root to python path
sys.path.append(os.getcwd())

from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch, AsyncMock
from src.app.main import app
from src.app.models.user import User, UserRole

# Mock Supabase Service
mock_supabase_service = MagicMock()
mock_supabase_user = MagicMock()
mock_supabase_user.email = "admin@example.com"
mock_supabase_user.id = "supabase-uid-123"
mock_supabase_service.get_user_by_token.return_value = mock_supabase_user

# Mock Database Session
mock_session = AsyncMock()
mock_result = MagicMock()
mock_user = User(
    email="admin@example.com",
    role=UserRole.SUPER_ADMIN.value,
    institution_id=None,
    is_active=True,
    id="supabase-uid-123",
)
mock_result.scalar_one_or_none.return_value = mock_user
mock_session.execute.return_value = mock_result

async def run_unified_auth():
    print("Starting Unified Auth Test...")
    
    with patch("src.app.api.routes.auth.SupabaseService", return_value=mock_supabase_service):
        with patch("src.app.api.routes.auth.get_db_session") as mock_get_db:
             # Setup async context manager for session
            mock_get_db.return_value.__aenter__.return_value = mock_session
            
            client = TestClient(app)
            
            # Test Case 1: Admin Login with Supabase Token
            response = client.post(
                "/auth/supabase/token",
                json={"access_token": "valid-supabase-token"}
            )
            
            if response.status_code == 200:
                print("✅ Admin Login Success")
                data = response.json()
                print(f"   Token Type: {data.get('token_type')}")
                print(f"   Access Token: {data.get('access_token')[:20]}...")
            else:
                print(f"❌ Admin Login Failed: {response.status_code} - {response.text}")

            # Verify it called Supabase Service
            mock_supabase_service.get_user_by_token.assert_called_with("valid-supabase-token")
            print("✅ Verified Supabase Token Validation Call")

if __name__ == "__main__":
    asyncio.run(run_unified_auth())
