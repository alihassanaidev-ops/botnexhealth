import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.api.deps import get_current_institution_or_location_user
from src.app.api.rate_limit import limiter
from src.app.api.routes.universal import appointments
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import BookingResult


def _fake_user():
    return SimpleNamespace(
        id="user-1",
        role="LOCATION_ADMIN",
        institution_id="inst-1",
        location_id="loc-1",
        is_active=True,
    )


# Setup app with overrides

def get_test_app(mock_adapter):
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(appointments.router, prefix="/pms")

    app.dependency_overrides[get_institution_pms] = lambda: mock_adapter
    app.dependency_overrides[get_current_institution_or_location_user] = _fake_user
    return app


@pytest.fixture
def mock_adapter():
    adapter = AsyncMock()
    adapter.book_appointment = AsyncMock()
    adapter.cancel_appointment = AsyncMock()
    adapter.reschedule_appointment = AsyncMock()
    return adapter


def test_book_appointment_route(mock_adapter):
    app = get_test_app(mock_adapter)
    client = TestClient(app)

    mock_adapter.book_appointment.return_value = BookingResult(
        success=True,
        id="appt-123",
        source="test",
        status="confirmed",
    )

    payload = {
        "patient_id": "p1",
        "provider_id": "pr1",
        "slot_start": "2026-01-01T10:00:00Z",
    }

    response = client.post("/pms/appointments", json=payload)
    assert response.status_code == 200
    assert response.json()["id"] == "appt-123"
    mock_adapter.book_appointment.assert_awaited_once()


def test_book_appointment_validation_error(mock_adapter):
    app = get_test_app(mock_adapter)
    client = TestClient(app)

    # Missing required fields should trigger 422
    response = client.post("/pms/appointments", json={"patient_id": "p1"})
    assert response.status_code == 422


def test_cancel_appointment_route(mock_adapter):
    app = get_test_app(mock_adapter)
    client = TestClient(app)

    mock_adapter.cancel_appointment.return_value = BookingResult(
        success=True,
        id="appt-123",
        source="test",
        status="cancelled",
    )

    response = client.patch("/pms/appointments/appt-123/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    mock_adapter.cancel_appointment.assert_awaited_once_with("appt-123")


def test_cancel_appointment_failure(mock_adapter):
    app = get_test_app(mock_adapter)
    client = TestClient(app)

    mock_adapter.cancel_appointment.return_value = BookingResult(
        success=False,
        id="appt-123",
        source="test",
        status="error",
        error="Failed",
    )

    response = client.patch("/pms/appointments/appt-123/cancel")
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["error"] == "Failed"
