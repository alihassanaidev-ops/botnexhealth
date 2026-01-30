import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.app.api.routes import appointments
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.api.routes.base import verify_admin_key

# Setup app with overrides
def get_test_app(mock_client):
    app = FastAPI()
    app.include_router(appointments.router, prefix="/nexhealth")
    
    async def override_get_client():
        return mock_client
        
    async def override_auth():
        return True

    app.dependency_overrides[get_nexhealth_client_dependency] = override_get_client
    app.dependency_overrides[verify_admin_key] = override_auth
    return app

@pytest.fixture
def mock_nh_client():
    client = AsyncMock()
    # Mock post/patch methods
    client.post = AsyncMock()
    client.patch = AsyncMock()
    return client

def test_book_appointment_route(mock_nh_client):
    app = get_test_app(mock_nh_client)
    client = TestClient(app)
    
    mock_nh_client.post.return_value = {
        "code": True,
        "data": {"appt": {"id": 123}}
    }
    
    payload = {
        "appt": {
            "start_time": "2023-01-01T10:00:00",
            "provider_id": 1,
            "patient_id": 2
        }
    }
    
    response = client.post("/nexhealth/appointments?subdomain=test&location_id=1", json=payload)
    assert response.status_code == 200
    assert response.json()["data"]["appt"]["id"] == 123
    mock_nh_client.post.assert_called_once()

def test_book_appointment_missing_subdomain(mock_nh_client):
    app = get_test_app(mock_nh_client)
    client = TestClient(app)
    
    # Provide valid body so we hit the subdomain check, not Pydantic validation error (422)
    payload = {
        "appt": {
            "start_time": "2023-01-01T10:00:00",
            "provider_id": 1,
            "patient_id": 2
        }
    }
    response = client.post("/nexhealth/appointments", json=payload)
    # The route requires subdomain query param:
    # subdomain: str | None = Query(None)
    # But checks: 'if not subdomain: raise 400'
    assert response.status_code == 400
    assert "Missing subdomain or location_id parameters" in response.json()["detail"]

def test_cancel_appointment_route(mock_nh_client):
    app = get_test_app(mock_nh_client)
    client = TestClient(app)
    
    mock_nh_client.patch.return_value = {"code": True}
    
    payload = {"appt": {"cancelled": True}}
    response = client.patch("/nexhealth/appointments/123?subdomain=test", json=payload)
    assert response.status_code == 200
    mock_nh_client.patch.assert_called_once()

def test_cancel_appointment_failure(mock_nh_client):
    app = get_test_app(mock_nh_client)
    client = TestClient(app)
    
    mock_nh_client.patch.return_value = {"code": False, "message": "Failed"}
    
    response = client.patch("/nexhealth/appointments/123?subdomain=test", json={"appt": {}})
    # If route calls handle_nexhealth_request, it returns whatever dict
    # If using ResponseModel, it might validate.
    # Route logic: return await handle_nexhealth_request(...)
    assert response.status_code == 200
    assert response.json()["code"] is False
