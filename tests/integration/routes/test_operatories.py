import pytest
from src.app.api.models import OperatoryListResponse, OperatoryDetailResponse

@pytest.mark.asyncio
async def test_list_operatories(async_client):
    """Test listing operatories."""
    print("\nListing operatories...")
    
    # 1. Get institution
    inst_response = await async_client.get("/api/v1/nexhealth/institutions")
    assert inst_response.status_code == 200
    inst_data = inst_response.json()
    
    if not inst_data.get("data"):
        pytest.skip("No institutions found")
    
    subdomain = inst_data["data"][0].get("subdomain")

    # 2. Get location
    loc_params = {"subdomain": subdomain} if subdomain else {}
    loc_response = await async_client.get("/api/v1/nexhealth/locations", params=loc_params)
    assert loc_response.status_code == 200
    loc_data = loc_response.json()
    
    if not loc_data.get("data"):
        pytest.skip("No locations found")
        
    location_id = None
    for inst in loc_data["data"]:
        if inst.get("locations"):
            location_id = inst["locations"][0]["id"]
            break
            
    if not location_id:
        pytest.skip("No location ID found")
        
    params = {
        "subdomain": subdomain,
        "location_id": location_id
    }
    
    response = await async_client.get("/api/v1/nexhealth/operatories", params=params)
    assert response.status_code == 200
    data = response.json()
    
    try:
        model = OperatoryListResponse(**data)
        assert model.data is not None
        print(f"Validation successful. Found {len(model.data)} operatories.")
    except Exception as e:
        pytest.fail(f"Failed to validate OperatoryListResponse: {e}")

@pytest.mark.asyncio
async def test_get_operatory_detail(async_client):
    """Test getting single operatory."""
    # Setup params
    inst_response = await async_client.get("/api/v1/nexhealth/institutions")
    assert inst_response.status_code == 200
    inst_data = inst_response.json()
    
    subdomain = None
    if inst_data.get("data"):
        subdomain = inst_data["data"][0].get("subdomain")

    loc_response = await async_client.get("/api/v1/nexhealth/locations", params={"subdomain": subdomain} if subdomain else {})
    assert loc_response.status_code == 200
    loc_data = loc_response.json()
    
    location_id = None
    if loc_data.get("data"):
        for inst in loc_data["data"]:
            if inst.get("locations"):
                location_id = inst["locations"][0]["id"]
                break
    
    if not location_id:
        pytest.skip("No location found")
        
    params = {"subdomain": subdomain, "location_id": location_id}
    list_response = await async_client.get("/api/v1/nexhealth/operatories", params=params)
    assert list_response.status_code == 200
    list_data = list_response.json()
    
    if not list_data.get("data"):
        pytest.skip("No operatories found")
        
    op_id = list_data["data"][0]["id"]
    print(f"\nFetching operatory details for ID: {op_id}")
    
    detail_params = {"subdomain": subdomain} if subdomain else {}
    response = await async_client.get(f"/api/v1/nexhealth/operatories/{op_id}", params=detail_params)
    assert response.status_code == 200
    data = response.json()
    
    try:
        model = OperatoryDetailResponse(**data)
        assert model.data.id == op_id
    except Exception as e:
        pytest.fail(f"Failed to validate OperatoryDetailResponse: {e}")
