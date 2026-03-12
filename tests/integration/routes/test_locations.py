import os
import pytest

from src.app.api.models import InstitutionBasicListResponse, LocationDetailResponse, AppointmentDescriptorListResponse
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
async def test_list_locations(async_client):
    """Test listing locations."""
    print("\nListing locations...")
    
    # Try fetching institutions first to get a valid subdomain
    inst_response = await async_client.get("/api/v1/nexhealth/institutions")
    assert inst_response.status_code == 200
    inst_data = inst_response.json()
    
    subdomain = None
    if inst_data.get("data"):
        subdomain = inst_data["data"][0].get("subdomain")
        print(f"Using subdomain: {subdomain}")

    params = {}
    if subdomain:
        params["subdomain"] = subdomain

    response = await async_client.get("/api/v1/nexhealth/locations", params=params)
    assert response.status_code == 200
    data = response.json()
    
    # Verify validation
    try:
        model = InstitutionBasicListResponse(**data)
        assert model.data is not None
        print(f"Validation successful. Found {len(model.data)} institution groups for locations.")
    except Exception as e:
        pytest.fail(f"Failed to validate InstitutionBasicListResponse: {e}")

@pytest.mark.asyncio
async def test_get_location_detail(async_client):
    """Test getting a single location."""
    # First get list to find an ID
    list_response = await async_client.get("/api/v1/nexhealth/locations")
    assert list_response.status_code == 200
    list_data = list_response.json()
    
    location_id = None
    # Data is list of institutions, each has locations
    if list_data.get("data"):
        for inst in list_data["data"]:
            if inst.get("locations"):
                location_id = inst["locations"][0]["id"]
                break
    
    if not location_id:
        pytest.skip("No locations found to test detail view")
        
    print(f"\nFetching location details for ID: {location_id}")
    
    response = await async_client.get(f"/api/v1/nexhealth/locations/{location_id}")
    assert response.status_code == 200
    data = response.json()
    
    try:
        model = LocationDetailResponse(**data)
        assert model.data.id == location_id
        print(f"Validation successful for location {location_id}")
    except Exception as e:
        pytest.fail(f"Failed to validate LocationDetailResponse: {e}")

@pytest.mark.asyncio
async def test_location_appointment_descriptors(async_client):
    """Test listing appointment descriptors for a location."""
    # Find a location first
    list_response = await async_client.get("/api/v1/nexhealth/locations")
    assert list_response.status_code == 200
    list_data = list_response.json()
    
    location_id = None
    if list_data.get("data"):
        for inst in list_data["data"]:
            if inst.get("locations"):
                location_id = inst["locations"][0]["id"]
                break
    
    if not location_id:
        pytest.skip("No locations found to test descriptors")

    print(f"\nFetching appointment descriptors for location ID: {location_id}")
    response = await async_client.get(f"/api/v1/nexhealth/locations/{location_id}/appointment_descriptors")
    assert response.status_code == 200
    data = response.json()
    
    try:
        model = AppointmentDescriptorListResponse(**data)
        assert model.data is not None
        print(f"Validation successful. Found {len(model.data)} descriptors.")
    except Exception as e:
        pytest.fail(f"Failed to validate AppointmentDescriptorListResponse: {e}")
