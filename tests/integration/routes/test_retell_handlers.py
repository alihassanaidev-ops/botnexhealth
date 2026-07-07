import pytest
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.pms.models import PatientCreateRequest
from src.app.retell.handlers import create_patient, list_transfer_numbers

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
