import os
import pytest

from src.app.api.models import InstitutionListResponse, InstitutionDetailResponse
from src.app.api.deps import get_current_admin
from src.app.main import app
from src.app.models.user import User, UserRole

pytestmark = pytest.mark.integration

if os.getenv("RUN_LIVE_NEXHEALTH") != "1":
    pytest.skip(
        "Live NexHealth tests disabled. Set RUN_LIVE_NEXHEALTH=1 to enable.",
        allow_module_level=True,
    )


@pytest.fixture(autouse=True)
def override_admin():
    mock_user = User(
        id="00000000-0000-0000-0000-000000000000",
        email="admin@example.com",
        role=UserRole.SUPER_ADMIN.value,
        is_active=True,
    )
    app.dependency_overrides[get_current_admin] = lambda: mock_user
    try:
        yield
    finally:
        app.dependency_overrides = {}

@pytest.mark.asyncio
async def test_list_institutions(async_client):
    """Test listing institutions."""
    print("\nListing institutions...")
    response = await async_client.get("/api/v1/nexhealth/institutions")
    assert response.status_code == 200
    
    data = response.json()
    
    # Verify response structure
    assert "data" in data
    assert isinstance(data["data"], list)
    
    # Verify validation
    try:
        model = InstitutionListResponse(**data)
        assert model.data is not None
        print(f"Validation successful. Found {len(model.data)} institutions.")
    except Exception as e:
        pytest.fail(f"Failed to validate InstitutionListResponse: {e}")

@pytest.mark.asyncio
async def test_get_institution_detail(async_client):
    """Test getting a single institution."""
    # First get list to find an ID
    list_response = await async_client.get("/api/v1/nexhealth/institutions")
    assert list_response.status_code == 200
    list_data = list_response.json()
    
    if not list_data.get("data"):
        pytest.skip("No institutions found to test detail view")
        
    institution_id = list_data["data"][0]["id"]
    print(f"\nFetching institution details for ID: {institution_id}")
    
    response = await async_client.get(f"/api/v1/nexhealth/institutions/{institution_id}")
    assert response.status_code == 200
    data = response.json()
    
    # Verify validation
    try:
        model = InstitutionDetailResponse(**data)
        assert model.data.id == institution_id
        print(f"Validation successful for institution {institution_id}")
    except Exception as e:
        pytest.fail(f"Failed to validate InstitutionDetailResponse: {e}")
