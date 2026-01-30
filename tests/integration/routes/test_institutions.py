import pytest
from src.app.api.models import InstitutionListResponse, InstitutionDetailResponse

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
