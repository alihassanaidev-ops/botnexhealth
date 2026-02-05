import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.app.retell import handlers

@pytest.fixture
def mock_client():
    return AsyncMock()

@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.nexhealth_subdomain = "test-subdomain"
    settings.nexhealth_location_id = 123
    return settings

# Helper to setup async route mocks
def setup_async_mock(mock_obj, method_name, return_value=None, side_effect=None):
    async_mock = AsyncMock()
    if return_value is not None:
        async_mock.return_value = return_value
    if side_effect is not None:
        async_mock.side_effect = side_effect
    setattr(mock_obj, method_name, async_mock)

@pytest.mark.asyncio
async def test_resolve_subdomain_removed():
    # Verify the helper is actually gone or not used (this is just a sanity check if we had left it)
    assert not hasattr(handlers, "_resolve_subdomain")

@pytest.mark.asyncio
async def test_lookup_patient_missing_args():
    args = {}
    result = await handlers.lookup_patient(args)
    assert "Please provide at least one search criterion" in result["message"]

@pytest.mark.asyncio
async def test_lookup_patient_missing_location_subdomain():
    args = {"name": "Test"}
    result = await handlers.lookup_patient(args)
    assert "I need to know which practice location and subdomain" in result["message"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.patient_routes")
async def test_lookup_patient_success(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    
    # Mock response model (dict)
    setup_async_mock(mock_routes, "list_patients", return_value={
        "data": {
            "patients": [
                {
                    "id": 1,
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john@example.com",
                    "phone_number": "555-5555",
                    "bio": {"date_of_birth": "1990-01-01"}
                }
            ]
        }
    })
    
    args = {
        "name": "John",
        "subdomain": "test",
        "location_id": 123
    }
    result = await handlers.lookup_patient(args)
    assert result["count"] == 1
    assert result["patients"][0]["first_name"] == "John"
    
@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.patient_routes")
async def test_lookup_patient_none_found(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_routes, "list_patients", return_value={"data": {"patients": []}})
    
    args = {
        "name": "John",
        "subdomain": "test",
        "location_id": 123
    }
    result = await handlers.lookup_patient(args)
    assert "No patients found" in result["message"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.patient_routes")
async def test_lookup_patient_error(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_routes, "list_patients", side_effect=Exception("API Error"))
    
    args = {
        "name": "John",
        "subdomain": "test",
        "location_id": 123
    }
    result = await handlers.lookup_patient(args)
    assert "trouble accessing the patient records" in result["message"]

# --- Create Patient ---
@pytest.mark.asyncio
async def test_create_patient_missing_fields():
    args = {"first_name": "John"}
    result = await handlers.create_patient(args)
    assert "is required" in result["error"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.patient_routes")
async def test_create_patient_success(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    
    setup_async_mock(mock_routes, "create_patient", return_value={
        "code": True, # Simulate success behavior from handle_nexhealth_request if strictly dict
        # Actually our route returns dict from handle_nexhealth_request.
        # handlers.py checks if response.get("code") is False.
        "data": {
            "user": {
                "id": 99,
                "first_name": "New"
            }
        }
    })

    args = {
        "first_name": "New",
        "last_name": "Patient",
        "email": "new@example.com",
        "phone_number": "555-0000",
        "date_of_birth": "2000-01-01",
        "location_id": 123,
        "subdomain": "test",
        "provider_id": 456
    }
    result = await handlers.create_patient(args)
    assert result["success"] is True
    assert result["patient_id"] == 99

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.patient_routes")
async def test_create_patient_failure_response(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_routes, "create_patient", return_value={"code": False, "error": "Bad Request"})
    
    args = {
        "first_name": "New", "last_name": "P", "email": "e", "phone_number": "p", 
        "date_of_birth": "d", "location_id": 1, "subdomain": "s", "provider_id": 1
    }
    result = await handlers.create_patient(args)
    assert result["success"] is False
    assert result["error"] == "Bad Request"

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.patient_routes")
async def test_create_patient_exception(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_routes, "create_patient", side_effect=Exception("Boom"))
    
    args = {
        "first_name": "New", "last_name": "P", "email": "e", "phone_number": "p", 
        "date_of_birth": "d", "location_id": 1, "subdomain": "s", "provider_id": 1
    }
    result = await handlers.create_patient(args)
    assert result["success"] is False
    assert "Boom" in result["error"]

# --- Find Slots ---
@pytest.mark.asyncio
async def test_find_slots_missing_args():
    args = {}
    result = await handlers.find_appointment_slots(args)
    assert "are required" in result["error"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.provider_routes")
@patch("src.app.retell.handlers.slot_routes")
async def test_find_slots_success_auto_providers(mock_slot_routes, mock_prov_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    
    # Mock provider auto-fetch (uses client.get directly in handlers.py)
    mock_client.get.return_value = {
        "data": [{"id": 10}, {"id": 11}]
    }
    
    # Mock slots
    setup_async_mock(mock_slot_routes, "list_appointment_slots", return_value={
        "data": [{"time": "2023-01-01T10:00:00"}]
    })
    
    args = {
        "start_date": "2023-01-01",
        "location_id": 123,
        "subdomain": "test",
        # No provider_id, triggers auto-fetch
    }
    result = await handlers.find_appointment_slots(args)
    assert result["slots_count"] == 1
    assert result["message"] == "Found 1 available slot(s)."

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.provider_routes")
@patch("src.app.retell.handlers.slot_routes")
async def test_find_slots_with_provider(mock_slot_routes, mock_prov_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_slot_routes, "list_appointment_slots", return_value={"data": []})
    
    args = {
        "start_date": "2023-01-01",
        "location_id": 123,
        "subdomain": "test",
        "provider_id": 999
    }
    result = await handlers.find_appointment_slots(args)
    # mock_client.get shouldn't be called for providers
    mock_slot_routes.list_appointment_slots.assert_called()
    assert result["slots_count"] == 0

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.provider_routes")
@patch("src.app.retell.handlers.slot_routes")
async def test_find_slots_exception(mock_slot_routes, mock_prov_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_slot_routes, "list_appointment_slots", side_effect=Exception("Fail"))
    
    args = {"start_date": "d", "location_id": 1, "subdomain": "s", "provider_id": 1}
    result = await handlers.find_appointment_slots(args)
    assert "Failed to find slots" in result["error"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.provider_routes")
@patch("src.app.retell.handlers.slot_routes")
async def test_find_slots_auto_provider_fail(mock_slot_routes, mock_prov_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    
    # Mock fetch fail
    mock_client.get.side_effect = Exception("Prov Fail")
    
    setup_async_mock(mock_slot_routes, "list_appointment_slots", return_value={"data": []})
    
    args = {"start_date": "d", "location_id": 1, "subdomain": "s"}
    # Should catch logger warning and proceed with empty list
    await handlers.find_appointment_slots(args)
    mock_slot_routes.list_appointment_slots.assert_called()

# --- Book Appointment ---
@pytest.mark.asyncio
async def test_book_missing_args():
    args = {"subdomain": "s"}
    result = await handlers.book_appointment(args)
    assert "is required" in result["error"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.appt_routes")
async def test_book_success(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    
    setup_async_mock(mock_routes, "book_appointment", return_value={
        "data": {
            "appt": {
                "id": 100,
                "start_time": "2023-01-01"
            }
        }
    })
    
    args = {
        "subdomain": "s", "location_id": 1, "patient_id": 1, "provider_id": 1, "start_time": "t"
    }
    result = await handlers.book_appointment(args)
    assert result["success"] is True
    assert result["appointment_id"] == 100

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.appt_routes")
async def test_book_fail_response(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_routes, "book_appointment", return_value={"code": False, "error": "Book Error"})
    
    args = {"subdomain": "s", "location_id": 1, "patient_id": 1, "provider_id": 1, "start_time": "t"}
    result = await handlers.book_appointment(args)
    assert result["success"] is False
    assert result["error"] == "Book Error"

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.appt_routes")
async def test_book_exception(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_routes, "book_appointment", side_effect=Exception("Book Exception"))
    
    args = {"subdomain": "s", "location_id": 1, "patient_id": 1, "provider_id": 1, "start_time": "t"}
    result = await handlers.book_appointment(args)
    assert result["success"] is False
    assert "Book Exception" in result["error"]

# --- Cancel Appointment ---
@pytest.mark.asyncio
async def test_cancel_missing_args():
    args = {}
    result = await handlers.cancel_appointment(args)
    assert "are required" in result["error"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.appt_routes")
async def test_cancel_success(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_routes, "cancel_appointment", return_value={"code": True})
    
    args = {"appointment_id": 1, "subdomain": "s"}
    result = await handlers.cancel_appointment(args)
    assert result["success"] is True

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.appt_routes")
async def test_cancel_fail(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_routes, "cancel_appointment", return_value={"code": False})
    
    args = {"appointment_id": 1, "subdomain": "s"}
    result = await handlers.cancel_appointment(args)
    assert result["success"] is False

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.appt_routes")
async def test_cancel_exception(mock_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    setup_async_mock(mock_routes, "cancel_appointment", side_effect=Exception("X"))
    
    args = {"appointment_id": 1, "subdomain": "s"}
    result = await handlers.cancel_appointment(args)
    assert result["success"] is False

# --- Reschedule ---
@pytest.mark.asyncio
@patch("src.app.retell.handlers.cancel_appointment")
@patch("src.app.retell.handlers.book_appointment")
async def test_reschedule_success(mock_book, mock_cancel):
    mock_cancel.return_value = {"success": True}
    mock_book.return_value = {"success": True}
    
    args = {"old_appointment_id": 1, "subdomain": "s"}
    result = await handlers.reschedule_appointment(args)
    assert result["success"] is True
    assert "Rescheduled successfully" in result["message"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers.cancel_appointment")
async def test_reschedule_cancel_fail(mock_cancel):
    mock_cancel.return_value = {"success": False, "error": "Fail"}
    args = {"old_appointment_id": 1, "subdomain": "s"}
    result = await handlers.reschedule_appointment(args)
    assert result["success"] is False
    assert "Failed to cancel" in result["error"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers.cancel_appointment")
@patch("src.app.retell.handlers.book_appointment")
async def test_reschedule_book_fail(mock_book, mock_cancel):
    mock_cancel.return_value = {"success": True}
    mock_book.return_value = {"success": False, "error": "Book Fail"}
    
    args = {"old_appointment_id": 1, "subdomain": "s"}
    result = await handlers.reschedule_appointment(args)
    assert result["success"] is False
    assert "failed to book" in result["error"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers.cancel_appointment")
@patch("src.app.retell.handlers.book_appointment")
async def test_reschedule_already_cancelled(mock_book, mock_cancel):
    # If cancel fails because it's already cancelled, we should proceed
    mock_cancel.return_value = {"success": False, "error": "Appointment already cancelled"}
    mock_book.return_value = {"success": True}
    
    args = {"old_appointment_id": 1, "subdomain": "s"}
    result = await handlers.reschedule_appointment(args)
    assert result["success"] is True

# --- Get Location Details ---
@pytest.mark.asyncio
async def test_get_loc_missing_id():
    result = await handlers.get_location_details({})
    assert "is required" in result["error"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.location_routes")
async def test_get_loc_success(mock_loc_routes, mock_get_client, mock_client):
    mock_get_client.return_value = mock_client
    setup_async_mock(mock_loc_routes, "get_location", return_value={
        "data": {
            "name": "Practice",
            "hours": "9-5"
        }
    })
    result = await handlers.get_location_details({"location_id": 1})
    assert result["practice_name"] == "Practice"

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.location_routes")
async def test_get_loc_fail(mock_loc_routes, mock_get_client, mock_client):
    mock_get_client.return_value = mock_client
    setup_async_mock(mock_loc_routes, "get_location", side_effect=Exception("Loc Fail"))
    result = await handlers.get_location_details({"location_id": 1})
    assert "Failed to retrieve" in result["error"]

# --- List Locations ---
@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.location_routes")
async def test_list_locs_success(mock_loc_routes, mock_get_client, mock_client):
    mock_get_client.return_value = mock_client
    setup_async_mock(mock_loc_routes, "list_locations", return_value={
        "data": [
            {
                "subdomain": "s1",
                "locations": [{"id": 1, "name": "L1"}]
            }
        ]
    })
    result = await handlers.list_locations({})
    assert result["count"] == 1
    assert result["locations"][0]["name"] == "L1"

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.location_routes")
async def test_list_locs_fail(mock_loc_routes, mock_get_client, mock_client):
    mock_get_client.return_value = mock_client
    setup_async_mock(mock_loc_routes, "list_locations", side_effect=Exception("Fail"))
    result = await handlers.list_locations({})
    assert "Failed to list" in result["error"]

# --- List Providers ---
@pytest.mark.asyncio
async def test_list_prov_missing_id():
    result = await handlers.list_providers({})
    assert "are required" in result["error"]

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.provider_routes")
async def test_list_prov_success(mock_prov_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    
    # handlers.list_providers uses client.get directly via auto-pagination
    mock_client.get.return_value = {
        "data": [{"id": 1, "name": "Doc"}]
    }
    
    args = {"location_id": 1, "subdomain": "s"}
    result = await handlers.list_providers(args)
    assert result["count"] == 1
    assert result["providers"][0]["name"] == "Doc"

@pytest.mark.asyncio
@patch("src.app.retell.handlers._get_nexhealth_client")
@patch("src.app.retell.handlers.get_settings")
@patch("src.app.retell.handlers.provider_routes")
async def test_list_prov_exception(mock_prov_routes, mock_get_settings, mock_get_client, mock_client, mock_settings):
    mock_get_client.return_value = mock_client
    mock_get_settings.return_value = mock_settings
    
    mock_client.get.side_effect = Exception("Fail")
    
    args = {"location_id": 1, "subdomain": "s"}
    result = await handlers.list_providers(args)
    assert "Failed to list" in result["error"]
