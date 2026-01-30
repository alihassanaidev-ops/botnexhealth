import pytest
from src.app.api.models import ProviderListResponse, ProviderDetailResponse

@pytest.mark.asyncio
async def test_list_providers(async_client):
    """Test listing providers."""
    print("\nListing providers...")
    
    # Fetch locations to get a valid subdomain
    loc_response = await async_client.get("/api/v1/nexhealth/locations")
    assert loc_response.status_code == 200
    loc_data = loc_response.json()
    
    subdomain = None
    if loc_data.get("data"):
        # data is list of institutions
        subdomain = loc_data["data"][0].get("subdomain")
        print(f"Using subdomain from locations: {subdomain}")

    params = {}
    if subdomain:
        params["subdomain"] = subdomain

    response = await async_client.get("/api/v1/nexhealth/providers", params=params)
    assert response.status_code == 200
    data = response.json()
    
    # Verify validation
    try:
        model = ProviderListResponse(**data)
        assert model.data is not None
        print(f"Validation successful. Found {len(model.data)} providers.")
    except Exception as e:
        pytest.fail(f"Failed to validate ProviderListResponse: {e}")

@pytest.mark.asyncio
async def test_get_provider_detail(async_client):
    """Test getting a single provider."""
    # First get list to find an ID
    # Get locations first to ensure we have subdomain
    loc_response = await async_client.get("/api/v1/nexhealth/locations")
    assert loc_response.status_code == 200
    loc_data = loc_response.json()
    
    subdomain = None
    if loc_data.get("data"):
        subdomain = loc_data["data"][0].get("subdomain")
        
    params = {}
    if subdomain:
        params["subdomain"] = subdomain

    list_response = await async_client.get("/api/v1/nexhealth/providers", params=params)
    assert list_response.status_code == 200
    list_data = list_response.json()
    
    if not list_data.get("data"):
        pytest.skip("No providers found to test detail view")
        
    provider_id = list_data["data"][0]["id"]
    print(f"\nFetching provider details for ID: {provider_id}")
    
    response = await async_client.get(f"/api/v1/nexhealth/providers/{provider_id}", params=params)
    assert response.status_code == 200
    data = response.json()
    
    try:
        model = ProviderDetailResponse(**data)
        assert model.data.id == provider_id
        print(f"Validation successful for provider {provider_id}")
    except Exception as e:
        pytest.fail(f"Failed to validate ProviderDetailResponse: {e}")
