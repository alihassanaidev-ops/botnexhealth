"""Tests for refactored Retell handlers (PMS adapter pattern)."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.app.pms.models import (
    BookingResult,
    UniversalAppointmentType,
    UniversalLocation,
    UniversalOperatory,
    UniversalPatient,
    UniversalProvider,
    UniversalSlot,
)
from src.app.retell import handlers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_adapter() -> AsyncMock:
    """Return a mock PMSAdapter with all async methods."""
    adapter = AsyncMock()
    adapter.source = "test"
    return adapter


def _patient(**overrides) -> UniversalPatient:
    defaults = {
        "id": "t-1",
        "source": "test",
        "first_name": "John",
        "last_name": "Doe",
        "email": "john@example.com",
        "phone": "555-5555",
        "date_of_birth": "1990-01-01",
        "extra": {},
    }
    defaults.update(overrides)
    return UniversalPatient(**defaults)


# All tests patch _get_adapter and log_audit_background
_ADAPTER_PATCH = "src.app.retell.handlers._get_adapter"
_AUDIT_PATCH = "src.app.services.audit_decorator.log_audit_background"


# ---------------------------------------------------------------------------
# Sanity – old helpers removed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_subdomain_removed():
    assert not hasattr(handlers, "_resolve_subdomain")
    assert not hasattr(handlers, "_get_nexhealth_client")


# ---------------------------------------------------------------------------
# lookup_patient
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_lookup_patient_missing_args(mock_get_adapter, mock_audit):
    mock_get_adapter.return_value = _mock_adapter()
    result = await handlers.lookup_patient({})
    assert "Please provide at least one search criterion" in result["message"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_lookup_patient_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.search_patients.return_value = [_patient()]
    mock_get_adapter.return_value = adapter

    result = await handlers.lookup_patient({"name": "John"})
    assert result["count"] == 1
    assert result["patients"][0]["first_name"] == "John"


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_lookup_patient_none_found(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.search_patients.return_value = []
    mock_get_adapter.return_value = adapter

    result = await handlers.lookup_patient({"name": "John"})
    assert "No patients found" in result["message"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_lookup_patient_adapter_error(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.search_patients.side_effect = Exception("API Error")
    mock_get_adapter.return_value = adapter

    result = await handlers.lookup_patient({"name": "John"})
    assert "trouble accessing the patient records" in result["message"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_lookup_patient_no_tenant(mock_get_adapter, mock_audit):
    mock_get_adapter.side_effect = ValueError("No tenant resolved")
    result = await handlers.lookup_patient({"name": "John"})
    assert "No tenant resolved" in result["message"]


# ---------------------------------------------------------------------------
# create_patient
# ---------------------------------------------------------------------------

_CREATE_ARGS = {
    "first_name": "New",
    "last_name": "Patient",
    "email": "new@example.com",
    "phone_number": "555-0000",
    "date_of_birth": "2000-01-01",
    "provider_id": "456",
}


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
async def test_create_patient_missing_fields(mock_audit):
    result = await handlers.create_patient({"first_name": "John"})
    assert "is required" in result["error"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_create_patient_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.create_patient.return_value = {"success": True, "patient_id": 99}
    mock_get_adapter.return_value = adapter

    result = await handlers.create_patient(_CREATE_ARGS)
    assert result["success"] is True
    assert result["patient_id"] == 99


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_create_patient_exception(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.create_patient.side_effect = Exception("Boom")
    mock_get_adapter.return_value = adapter

    result = await handlers.create_patient(_CREATE_ARGS)
    assert result["success"] is False
    assert "Boom" in result["error"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_create_patient_no_tenant(mock_get_adapter, mock_audit):
    mock_get_adapter.side_effect = ValueError("No tenant")
    result = await handlers.create_patient(_CREATE_ARGS)
    assert result["success"] is False


# ---------------------------------------------------------------------------
# find_appointment_slots
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
async def test_find_slots_missing_start_date(mock_audit):
    result = await handlers.find_appointment_slots({})
    assert "start_date is required" in result["error"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_find_slots_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.get_available_slots.return_value = [
        UniversalSlot(start="2023-01-01T10:00:00", end="2023-01-01T10:30:00", provider_id="p1")
    ]
    mock_get_adapter.return_value = adapter

    result = await handlers.find_appointment_slots({"start_date": "2023-01-01"})
    assert result["slots_count"] == 1
    assert result["message"] == "Found 1 available slot(s)."


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_find_slots_exception(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.get_available_slots.side_effect = Exception("Fail")
    mock_get_adapter.return_value = adapter

    result = await handlers.find_appointment_slots({"start_date": "2023-01-01"})
    assert "Failed to find slots" in result["error"]


# ---------------------------------------------------------------------------
# book_appointment
# ---------------------------------------------------------------------------

_BOOK_ARGS = {
    "patient_id": "1",
    "provider_id": "1",
    "start_time": "2023-01-01T10:00:00",
}


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
async def test_book_missing_args(mock_audit):
    result = await handlers.book_appointment({})
    assert "is required" in result["error"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_book_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.book_appointment.return_value = BookingResult(
        success=True, id="100", source="test", status="confirmed",
        start="2023-01-01T10:00:00",
    )
    mock_get_adapter.return_value = adapter

    result = await handlers.book_appointment(_BOOK_ARGS)
    assert result["success"] is True
    assert result["id"] == "100"


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_book_exception(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.book_appointment.side_effect = Exception("Book Exception")
    mock_get_adapter.return_value = adapter

    result = await handlers.book_appointment(_BOOK_ARGS)
    assert result["success"] is False
    assert "Book Exception" in result["error"]


# ---------------------------------------------------------------------------
# cancel_appointment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
async def test_cancel_missing_id(mock_audit):
    result = await handlers.cancel_appointment({})
    assert "appointment_id is required" in result["error"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_cancel_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.cancel_appointment.return_value = BookingResult(success=True, status="cancelled")
    mock_get_adapter.return_value = adapter

    result = await handlers.cancel_appointment({"appointment_id": "1"})
    assert result["success"] is True


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_cancel_exception(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.cancel_appointment.side_effect = Exception("X")
    mock_get_adapter.return_value = adapter

    result = await handlers.cancel_appointment({"appointment_id": "1"})
    assert result["success"] is False


# ---------------------------------------------------------------------------
# reschedule_appointment
# ---------------------------------------------------------------------------

_RESCHED_ARGS = {
    "old_appointment_id": "1",
    "patient_id": "1",
    "provider_id": "1",
    "start_time": "2023-01-02T10:00:00",
}


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
async def test_reschedule_missing_old_id(mock_audit):
    result = await handlers.reschedule_appointment({})
    assert "old_appointment_id is required" in result["error"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
async def test_reschedule_missing_booking_fields(mock_audit):
    result = await handlers.reschedule_appointment({"old_appointment_id": "1"})
    assert "is required for the new booking" in result["error"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_reschedule_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.reschedule_appointment.return_value = BookingResult(
        success=True, id="200", status="confirmed",
    )
    mock_get_adapter.return_value = adapter

    result = await handlers.reschedule_appointment(_RESCHED_ARGS)
    assert result["success"] is True
    assert result["id"] == "200"


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_reschedule_exception(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.reschedule_appointment.side_effect = Exception("Fail")
    mock_get_adapter.return_value = adapter

    result = await handlers.reschedule_appointment(_RESCHED_ARGS)
    assert result["success"] is False


# ---------------------------------------------------------------------------
# list_appointment_types
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_list_appt_types_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.list_appointment_types.return_value = [
        UniversalAppointmentType(
            id="at-1", source="test", name="Cleaning",
            duration_minutes=30, source_id="raw-1", source_metadata={"descriptor_ids": [1]},
        )
    ]
    mock_get_adapter.return_value = adapter

    result = await handlers.list_appointment_types({})
    assert result["count"] == 1
    assert result["appointment_types"][0]["name"] == "Cleaning"


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_list_appt_types_error(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.list_appointment_types.side_effect = Exception("Fail")
    mock_get_adapter.return_value = adapter

    result = await handlers.list_appointment_types({})
    assert "error" in result


# ---------------------------------------------------------------------------
# get_location_details
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
async def test_get_location_missing_id(mock_audit):
    result = await handlers.get_location_details({})
    assert "location_id is required" in result["error"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_get_location_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.get_location.return_value = UniversalLocation(
        id="loc-1", source="test", name="Practice",
    )
    mock_get_adapter.return_value = adapter

    result = await handlers.get_location_details({"location_id": "loc-1"})
    assert result["practice_name"] == "Practice"


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_get_location_not_found(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.get_location.return_value = None
    mock_get_adapter.return_value = adapter

    result = await handlers.get_location_details({"location_id": "loc-1"})
    assert "Location not found" in result["error"]


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_get_location_exception(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.get_location.side_effect = Exception("Loc Fail")
    mock_get_adapter.return_value = adapter

    result = await handlers.get_location_details({"location_id": "loc-1"})
    assert "Failed to retrieve" in result["error"]


# ---------------------------------------------------------------------------
# list_locations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_list_locations_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.list_locations.return_value = [
        UniversalLocation(id="loc-1", source="test", name="L1")
    ]
    mock_get_adapter.return_value = adapter

    result = await handlers.list_locations({})
    assert result["count"] == 1
    assert result["locations"][0]["name"] == "L1"


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_list_locations_error(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.list_locations.side_effect = Exception("Fail")
    mock_get_adapter.return_value = adapter

    result = await handlers.list_locations({})
    assert "Failed to list" in result["error"]


# ---------------------------------------------------------------------------
# list_providers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_list_providers_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.list_providers.return_value = [
        UniversalProvider(
            id="p-1", source="test", name="Doc", first_name="Dr", last_name="Doc",
        )
    ]
    mock_get_adapter.return_value = adapter

    result = await handlers.list_providers({})
    assert result["count"] == 1
    assert result["providers"][0]["name"] == "Doc"


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_list_providers_error(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.list_providers.side_effect = Exception("Fail")
    mock_get_adapter.return_value = adapter

    result = await handlers.list_providers({})
    assert "Failed to list" in result["error"]


# ---------------------------------------------------------------------------
# list_operatories
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_list_operatories_success(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.list_operatories.return_value = [
        UniversalOperatory(id="op-1", source="test", name="Chair 1", is_active=True)
    ]
    mock_get_adapter.return_value = adapter

    result = await handlers.list_operatories({})
    assert result["count"] == 1
    assert result["operatories"][0]["name"] == "Chair 1"


@pytest.mark.asyncio
@patch(_AUDIT_PATCH)
@patch(_ADAPTER_PATCH)
async def test_list_operatories_error(mock_get_adapter, mock_audit):
    adapter = _mock_adapter()
    adapter.list_operatories.side_effect = Exception("Fail")
    mock_get_adapter.return_value = adapter

    result = await handlers.list_operatories({})
    assert "Failed to list" in result["error"]
