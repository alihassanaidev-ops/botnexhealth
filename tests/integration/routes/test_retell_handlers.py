import pytest
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.pms.base import SupportsAppointmentConfirmation
from src.app.pms.models import BookingResult, PatientCreateRequest
from src.app.retell.handlers import (
    confirm_appointment,
    create_patient,
    list_transfer_numbers,
)


class ConfirmingAdapter(SupportsAppointmentConfirmation):
    def __init__(self, result: BookingResult | None = None) -> None:
        self.confirm_appointment_mock = AsyncMock(
            return_value=result
            or BookingResult(
                success=True,
                source="nexhealth",
                status="confirmed",
                message="Appointment confirmed successfully.",
            )
        )

    async def confirm_appointment(self, appointment_id: str) -> BookingResult:
        return await self.confirm_appointment_mock(appointment_id)

@pytest.mark.asyncio
async def test_create_patient_success():
    """Test successful patient creation via Retell handler."""
    mock_args = {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone_number": "555-0123",
        "date_of_birth": "1990-01-01",
        "provider_id": "456",
        "gender": "Other",
    }

    mock_response = {
        "success": True,
        "patient_id": "nh-789",
        "message": "Patient John created successfully.",
    }

    mock_adapter = SimpleNamespace(create_patient=AsyncMock(return_value=mock_response))

    async def mock_resolve():
        return SimpleNamespace(institution=SimpleNamespace(id="inst-1"), location=SimpleNamespace(id="loc-1"), adapter=mock_adapter)

    with patch("src.app.retell.handlers._resolve_context", new=mock_resolve):
        result = await create_patient(mock_args)

    assert result["success"] is True
    assert result["patient_id"] == "nh-789"
    assert "created successfully" in result["message"]

    # Verify adapter call structure
    mock_adapter.create_patient.assert_awaited_once()
    req = mock_adapter.create_patient.call_args.args[0]
    assert isinstance(req, PatientCreateRequest)
    assert req.first_name == "John"
    assert req.last_name == "Doe"
    assert req.email == "john.doe@example.com"
    assert req.phone == "555-0123"
    assert req.date_of_birth == "1990-01-01"
    assert req.provider_id == "456"
    assert req.gender == "Other"


@pytest.mark.asyncio
async def test_confirm_appointment_success():
    mock_adapter = ConfirmingAdapter()

    async def mock_resolve():
        return SimpleNamespace(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            adapter=mock_adapter,
        )

    with patch("src.app.retell.handlers._resolve_context", new=mock_resolve):
        result = await confirm_appointment({"appointment_id": "nh-appt-123"})

    assert result["success"] is True
    assert result["status"] == "confirmed"
    mock_adapter.confirm_appointment_mock.assert_awaited_once_with("nh-appt-123")


@pytest.mark.asyncio
async def test_confirm_appointment_requires_appointment_id():
    result = await confirm_appointment({})

    assert result == {"error": "appointment_id is required."}


@pytest.mark.asyncio
async def test_confirm_appointment_rejects_unsupported_pms():
    async def mock_resolve():
        return SimpleNamespace(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            adapter=SimpleNamespace(),
        )

    with patch("src.app.retell.handlers._resolve_context", new=mock_resolve):
        result = await confirm_appointment({"appointment_id": "appt-123"})

    assert result == {
        "success": False,
        "error": "Appointment confirmation is not supported for this PMS.",
    }

@pytest.mark.asyncio
async def test_create_patient_missing_fields():
    """Test validation failure for missing fields."""
    mock_args = {
        "first_name": "John",
        # Missing other required fields
    }

    result = await create_patient(mock_args)

    assert "error" in result
    assert "is required" in result["error"]

@pytest.mark.asyncio
async def test_create_patient_api_failure():
    """Test handling of upstream API failure."""
    mock_args = {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone_number": "555-0123",
        "date_of_birth": "1990-01-01",
        "provider_id": "456",
        "gender": "Female",
    }

    mock_adapter = SimpleNamespace(create_patient=AsyncMock(side_effect=Exception("API Error")))

    async def mock_resolve():
        return SimpleNamespace(institution=SimpleNamespace(id="inst-1"), location=SimpleNamespace(id="loc-1"), adapter=mock_adapter)

    with patch("src.app.retell.handlers._resolve_context", new=mock_resolve):
        result = await create_patient(mock_args)

    assert result["success"] is False
    assert result["error"] == "Failed to create patient"
    assert "API Error" not in result["error"]


@pytest.mark.asyncio
async def test_create_patient_requires_gender():
    result = await create_patient(
        {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "phone_number": "555-0123",
            "date_of_birth": "1990-01-01",
            "provider_id": "456",
        }
    )

    assert result == {"error": "gender is required."}


@pytest.mark.asyncio
async def test_create_patient_rejects_unsupported_gender():
    result = await create_patient(
        {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "phone_number": "555-0123",
            "date_of_birth": "1990-01-01",
            "provider_id": "456",
            "gender": "Unknown",
        }
    )

    assert result == {"error": "gender must be one of: Female, Male, Other."}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_gender", "expected_gender"),
    [
        ("male", "Male"),
        (" FEMALE ", "Female"),
        ("oThEr", "Other"),
    ],
)
async def test_create_patient_normalizes_gender(raw_gender, expected_gender):
    mock_adapter = SimpleNamespace(create_patient=AsyncMock(return_value={"success": True}))

    async def mock_resolve():
        return SimpleNamespace(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            adapter=mock_adapter,
        )

    with patch("src.app.retell.handlers._resolve_context", new=mock_resolve):
        result = await create_patient(
            {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john.doe@example.com",
                "phone_number": "555-0123",
                "date_of_birth": "1990-01-01",
                "provider_id": "456",
                "gender": raw_gender,
            }
        )

    assert result["success"] is True
    req = mock_adapter.create_patient.call_args.args[0]
    assert req.gender == expected_gender


@pytest.mark.asyncio
async def test_list_transfer_numbers_success():
    """Test listing transfer numbers via Retell handler."""
    mock_rows = [
        SimpleNamespace(phone_number="+15551230001", department="Reception"),
        SimpleNamespace(phone_number="+15551230002", department="Billing"),
    ]

    mock_session = AsyncMock()
    result_transfer = MagicMock()
    result_transfer.scalars.return_value.all.return_value = mock_rows

    result_hours = MagicMock()
    result_hours.scalars.return_value.all.return_value = [
        SimpleNamespace(day_of_week=0, is_open=True, open_time=None, close_time=None),
    ]

    result_breaks = MagicMock()
    result_breaks.scalars.return_value.all.return_value = []

    mock_session.execute.side_effect = [result_transfer, result_hours, result_breaks]

    @asynccontextmanager
    async def fake_db(*_args, **_kwargs):
        yield mock_session

    async def mock_resolve(require_pms: bool = True):
        return SimpleNamespace(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1", timezone="UTC"),
            adapter=SimpleNamespace(),
        )

    # Patch the binding in the handlers module, not in src.app.database — the
    # handlers module imports get_system_db_session via `from … import …`,
    # so the source-module attribute is no longer reached at call time.
    with patch("src.app.retell.handlers._resolve_context", new=mock_resolve), patch(
        "src.app.retell.handlers.get_system_db_session", new=fake_db
    ):
        result = await list_transfer_numbers({})

    assert result["count"] == 2
    assert result["transfer_numbers"][0]["phone_number"] == "+15551230001"
    assert result["transfer_numbers"][0]["department"] == "Reception"
