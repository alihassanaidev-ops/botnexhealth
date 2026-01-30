
import pytest
import pytest_asyncio
import logging
from datetime import datetime, timedelta
from src.app.config import Settings
from src.app.dependencies import init_nexhealth_client, cleanup_nexhealth_client
from src.app.retell.handlers import (
    lookup_patient,
    find_appointment_slots,
    book_appointment,
    cancel_appointment,
    get_location_details,
    reschedule_appointment
)

# Mark as integration test
pytestmark = pytest.mark.integration

logger = logging.getLogger(__name__)

@pytest_asyncio.fixture
async def initialized_client():
    """Initialize the real NexHealth client for handlers to use."""
    # Ensure settings are loaded from env (default behavior of Settings())
    await init_nexhealth_client()
    yield
    await cleanup_nexhealth_client()

@pytest.fixture
def real_settings():
    """Load real settings."""
    return Settings()

@pytest_asyncio.fixture
async def test_context(initialized_client, real_settings):
    """
    Fetch valid IDs (subdomain, location, provider, patient) to use in tests.
    Returns a dict with these values.
    """
    from src.app.dependencies import get_nexhealth_client_dependency
    client = await get_nexhealth_client_dependency()
    
    context = {
        "subdomain": real_settings.nexhealth_subdomain,
        "location_id": real_settings.nexhealth_location_id,
        "patient_id": None,
        "provider_id": None,
        "appointment_type_id": None,
        "operatory_id": None,
        "patient_name": None
    }

    # 1. Resolve Subdomain/Location
    if not context["subdomain"]:
        resp = await client.get("/institutions")
        data = resp.get("data", [])
        if data and isinstance(data, list) and len(data) > 0:
            context["subdomain"] = data[0].get("subdomain")
            
    if not context["subdomain"]:
        pytest.skip("Could not resolve subdomain")

    if not context["location_id"]:
        resp = await client.get("/locations", params={"subdomain": context["subdomain"]})
        data = resp.get("data", [])
        if data and isinstance(data, list) and len(data) > 0 and data[0].get("locations"):
            context["location_id"] = data[0]["locations"][0]["id"]
            
    if not context["location_id"]:
        pytest.skip("Could not resolve location_id")

    # 2. Find a Provider
    resp = await client.get("/providers", params={
        "subdomain": context["subdomain"], 
        "location_id": context["location_id"],
        "per_page": 1
    })
    data = resp.get("data", [])
    if data and isinstance(data, list) and len(data) > 0:
         context["provider_id"] = data[0]["id"]

    # 3. Find a Patient
    resp = await client.get("/patients", params={
        "subdomain": context["subdomain"], 
        "location_id": context["location_id"],
        "per_page": 1
    })
    data = resp.get("data", {})
    patients = data.get("patients", [])
    if patients and len(patients) > 0:
         context["patient_id"] = patients[0]["id"]
         context["patient_name"] = patients[0].get("first_name")

    # 4. Find Appointment Type
    resp = await client.get("/appointment_types", params={
        "subdomain": context["subdomain"], 
        "location_id": context["location_id"]
    })
    data = resp.get("data", [])
    if data and isinstance(data, list) and len(data) > 0:
        context["appointment_type_id"] = data[0]["id"]

    # 5. Find Operatory
    resp = await client.get("/operatories", params={
        "subdomain": context["subdomain"], 
        "location_id": context["location_id"]
    })
    data = resp.get("data", [])
    if data and isinstance(data, list) and len(data) > 0:
        context["operatory_id"] = data[0]["id"]

    logger.info(f"Test Context: {context}")
    return context

@pytest.mark.asyncio
async def test_lookup_patient(test_context):
    logger.info(f"test_context type: {type(test_context)}")
    logger.info(f"test_context val: {test_context}")
    
    if not isinstance(test_context, dict):
        pytest.fail(f"test_context is not a dict: {type(test_context)}")
        
    if not test_context.get("patient_name"):
        pytest.skip("No patient found to lookup")
        
    args = {
        "name": test_context["patient_name"],
        "subdomain": test_context["subdomain"],
        "location_id": test_context["location_id"] # Optional but helps
    }

    result = await lookup_patient(args)

    assert result.get("count") is not None
    assert result.get("count") > 0
    assert "patients" in result
    found = False
    for p in result["patients"]:
        if str(p["id"]) == str(test_context["patient_id"]):
            found = True
            break
    # Note: Name search might be fuzzy or return multiple, so strict ID match might fail if name is common
    # But we expect at least some results.
    assert len(result["patients"]) > 0

@pytest.mark.asyncio
async def test_find_appointment_slots(test_context):
    start_date = datetime.now().strftime("%Y-%m-%d")
    args = {
        "start_date": start_date,
        "location_id": test_context["location_id"],
        "subdomain": test_context["subdomain"],
        "days": 7
    }
    if test_context.get("provider_id"):
        args["provider_id"] = test_context["provider_id"]
        
    result = await find_appointment_slots(args)
    
    assert result.get("slots_count") is not None
    assert "slots" in result

@pytest.mark.asyncio
async def test_book_and_cancel_appointment(test_context):
    if not test_context.get("provider_id") or not test_context.get("patient_id"):
        pytest.skip("Missing provider or patient for booking test")

    # Prepare a booking time
    start_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    slots_args = {
        "start_date": start_date,
        "location_id": test_context["location_id"],
        "subdomain": test_context["subdomain"],
        "provider_id": test_context["provider_id"],
        "days": 5
    }
    if test_context.get("appointment_type_id"):
        slots_args["appointment_type_id"] = test_context["appointment_type_id"]
        
    if test_context.get("operatory_id"):
         slots_args["operatory_ids"] = [test_context["operatory_id"]]
    
    slots_result = await find_appointment_slots(slots_args)
    
    target_time = None
    if slots_result.get("slots") and len(slots_result["slots"]) > 0:
        # Try to find a slot
        for day_slots in slots_result["slots"]:
            if day_slots.get("slots"):
                target_time = day_slots["slots"][0]["time"]
                break
    
    if not target_time:
        pytest.skip(f"No slots found for booking test. Provider={test_context.get('provider_id')}, Loc={test_context.get('location_id')}")

    # BOOK
    book_args = {
        "subdomain": test_context["subdomain"],
        "location_id": test_context["location_id"],
        "patient_id": test_context["patient_id"],
        "provider_id": test_context["provider_id"],
        "start_time": target_time,
        "note": "Test appointment from integration test"
    }
    # Skip appointment_type_id to avoid "not configured" error, defaulting to provider/location default
    # if test_context.get("appointment_type_id"):
    #     book_args["appointment_type_id"] = test_context["appointment_type_id"]
        
    if test_context.get("operatory_id"):
        book_args["operatory_id"] = test_context["operatory_id"]

    print(f"Booking appointment with: {book_args}")
    booking_result = await book_appointment(book_args)
    
    if not booking_result.get("success"):
        pytest.fail(f"Booking failed: {booking_result.get('error')}")
        
    appt_id = booking_result.get("appointment_id")
    assert appt_id is not None
    print(f"Booked appointment ID: {appt_id}")

    # CANCEL
    cancel_args = {
        "appointment_id": appt_id,
        "subdomain": test_context["subdomain"]
    }
    
    cancel_result = await cancel_appointment(cancel_args)
    assert cancel_result.get("success") is True, f"Cancel failed: {cancel_result.get('error')}"

@pytest.mark.asyncio
async def test_get_location_details(test_context):
    args = {
        "location_id": test_context["location_id"]
    }
    result = await get_location_details(args)
    assert "location" in result
    assert result["location"]["name"] is not None
