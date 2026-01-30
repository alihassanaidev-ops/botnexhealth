import pytest
import pytest_asyncio
import datetime
from src.app.api.models import NexHealthResponse

@pytest_asyncio.fixture
async def appointment_context(async_client):
    """Setup context with subdomain, location, provider, patient."""
    context = {}
    
    # 1. Get institution
    inst_response = await async_client.get("/api/v1/nexhealth/institutions")
    if inst_response.status_code != 200 or not inst_response.json().get("data"):
        pytest.skip("No institutions found")
    
    context["subdomain"] = inst_response.json()["data"][0].get("subdomain")

    # 2. Get location
    loc_response = await async_client.get("/api/v1/nexhealth/locations", params={"subdomain": context["subdomain"]})
    if loc_response.status_code != 200 or not loc_response.json().get("data"):
         pytest.skip("No locations found")
         
    for inst in loc_response.json()["data"]:
        if inst.get("locations"):
            context["location_id"] = inst["locations"][0]["id"]
            break
            
    if not context.get("location_id"):
        pytest.skip("No location ID found")

    # 3. Get Provider
    prov_response = await async_client.get("/api/v1/nexhealth/providers", params={"subdomain": context["subdomain"], "location_id": context["location_id"]})
    if prov_response.status_code == 200 and prov_response.json().get("data"):
        context["provider_id"] = prov_response.json()["data"][0]["id"]
    else:
        pytest.skip("No provider found")

    # 4. Get Patient
    pat_response = await async_client.get("/api/v1/nexhealth/patients", params={"subdomain": context["subdomain"], "location_id": context["location_id"]})
    if pat_response.status_code == 200 and pat_response.json().get("data", {}).get("patients"):
        context["patient_id"] = pat_response.json()["data"]["patients"][0]["id"]
    else:
        pytest.skip("No patient found")
        
    return context

@pytest.mark.asyncio
async def test_list_appointments(async_client, appointment_context):
    """Test listing appointments."""
    print("\nListing appointments...")
    
    start_date = datetime.date.today().isoformat()
    end_date = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
    
    params = {
        "subdomain": appointment_context["subdomain"],
        "location_id": appointment_context["location_id"],
        "start": start_date,
        "end": end_date
    }
    
    response = await async_client.get("/api/v1/nexhealth/appointments", params=params)
    assert response.status_code == 200
    data = response.json()
    
    try:
        model = NexHealthResponse(**data)
        assert model.data is not None
        assert isinstance(model.data, list)
    except Exception as e:
        pytest.fail(f"Failed to validate response: {e}")
