import pytest
import datetime
from src.app.api.models import AppointmentSlotsResponse

@pytest.mark.asyncio
async def test_get_appointment_slots(async_client):
    """Test getting appointment slots."""
    print("\nGetting appointment slots...")
    
    # 1. Get institution for subdomain
    inst_response = await async_client.get("/api/v1/nexhealth/institutions")
    assert inst_response.status_code == 200
    inst_data = inst_response.json()
    
    if not inst_data.get("data"):
        pytest.skip("No institutions found")
    
    subdomain = inst_data["data"][0].get("subdomain")
    print(f"Using subdomain: {subdomain}")

    # Find a location and provider
    location_id = None
    provider_id = None
    
    # 2. Get providers for location logic
    prov_response = await async_client.get(
        "/api/v1/nexhealth/providers", 
        params={"subdomain": subdomain} if subdomain else {}
    )
    assert prov_response.status_code == 200
    prov_data = prov_response.json()
    
    if prov_data.get("data"):
        for prov in prov_data["data"]:
            if prov.get("locations"):
                location_id = prov["locations"][0]["id"]
                provider_id = prov["id"]
                break
                
    if not location_id or not provider_id:
        pytest.skip("No provider/location pair found")
        
    # 3. Get slots
    start_date = datetime.date.today().isoformat()
    
    params = {
        "start_date": start_date,
        "lids[]": [location_id],
        "pids[]": [provider_id],
        "days": 7
    }
    if subdomain:
        params["subdomain"] = subdomain
        
    print(f"Fetching slots for location {location_id} starting {start_date}")
    response = await async_client.get("/api/v1/nexhealth/appointment_slots", params=params)
    assert response.status_code == 200
    data = response.json()
    
    try:
        model = AppointmentSlotsResponse(**data)
        # Note: slots might be empty, so we just check the list isn't None
        assert model.data is not None 
        print(f"Validation successful. Found {len(model.data)} slot groups.")
        if model.data:
             print(f"First group next available: {model.data[0].next_available_date}")
    except Exception as e:
        pytest.fail(f"Failed to validate AppointmentSlotsResponse: {e}")
