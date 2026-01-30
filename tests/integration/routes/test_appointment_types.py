import pytest
from src.app.api.models import AppointmentTypeListResponse, AppointmentTypeDetailResponse

@pytest.mark.asyncio
async def test_list_appointment_types(async_client):
    """Test listing appointment types."""
    print("\nListing appointment types...")
    
    # Get locations to get valid subdomain and location_id
    loc_response = await async_client.get("/api/v1/nexhealth/locations")
    assert loc_response.status_code == 200
    loc_data = loc_response.json()
    
    subdomain = None
    location_id = None
    
    if loc_data.get("data"):
         for inst in loc_data["data"]:
             subdomain = inst.get("subdomain")
             if inst.get("locations"):
                 location_id = inst["locations"][0]["id"]
                 break
    
    params = {}
    if subdomain:
        params["subdomain"] = subdomain
    if location_id:
        params["location_id"] = location_id

    response = await async_client.get("/api/v1/nexhealth/appointment_types", params=params)
    assert response.status_code == 200
    data = response.json()
    
    try:
        model = AppointmentTypeListResponse(**data)
        assert model.data is not None
        print(f"Validation successful. Found {len(model.data)} appointment types.")
    except Exception as e:
        pytest.fail(f"Failed to validate AppointmentTypeListResponse: {e}")

@pytest.mark.asyncio
async def test_get_appointment_type_detail(async_client):
    """Test getting single appointment type."""
    # Get location and subdomain
    loc_response = await async_client.get("/api/v1/nexhealth/locations")
    assert loc_response.status_code == 200
    loc_data = loc_response.json()
    
    subdomain = None
    location_id = None
    
    if loc_data.get("data"):
         for inst in loc_data["data"]:
             subdomain = inst.get("subdomain")
             if inst.get("locations"):
                 location_id = inst["locations"][0]["id"]
                 break

    params = {}
    if subdomain:
        params["subdomain"] = subdomain
    if location_id:
        params["location_id"] = location_id

    list_response = await async_client.get("/api/v1/nexhealth/appointment_types", params=params)
    assert list_response.status_code == 200
    list_data = list_response.json()
    
    if not list_data.get("data"):
        pytest.skip("No appointment types found")
        
    appt_type_id = list_data["data"][0]["id"]
    print(f"\nFetching appointment type details for ID: {appt_type_id}")
    
    response = await async_client.get(f"/api/v1/nexhealth/appointment_types/{appt_type_id}", params=params)
    assert response.status_code == 200
    data = response.json()
    
    try:
        model = AppointmentTypeDetailResponse(**data)
        assert model.data.id == appt_type_id
    except Exception as e:
        pytest.fail(f"Failed to validate AppointmentTypeDetailResponse: {e}")
