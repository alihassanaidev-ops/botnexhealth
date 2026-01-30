
import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response
from src.app.main import app
from src.app.dependencies import get_settings
from src.app.config import Settings

@pytest.fixture
def override_settings(mock_settings):
    app.dependency_overrides[get_settings] = lambda: mock_settings
    mock_settings.admin_api_key = "test-admin-key"
    return mock_settings

@pytest.fixture
def test_client(override_settings):
    with TestClient(app) as client:
        yield client

def test_list_locations(test_client, mock_settings):
    with respx.mock(base_url=mock_settings.base_url) as respx_mock:
        # Mock Auth
        respx_mock.post("/authenticates").mock(
            return_value=Response(201, json={"code": True, "data": {"token": "token"}})
        )
        # Mock List Locations
        respx_mock.get("/locations").mock(
            return_value=Response(
                200, 
                json={
                    "code": True, 
                    "data": [
                        {
                            "id": 1, 
                            "name": "Institution 1", 
                            "subdomain": "sub1",
                            "locations": [
                                {"id": 101, "name": "Loc 1", "institution_id": 1}
                            ]
                        }
                    ]
                }
            )
        )
        
        response = test_client.get(
            "/api/v1/nexhealth/locations", 
            headers={"x-admin-api-key": "test-admin-key"}
        )
        
        assert response.status_code == 200
        assert len(response.json()["data"]) == 1
        assert response.json()["data"][0]["locations"][0]["id"] == 101

def test_get_location(test_client, mock_settings):
    with respx.mock(base_url=mock_settings.base_url) as respx_mock:
        respx_mock.post("/authenticates").mock(
            return_value=Response(201, json={"code": True, "data": {"token": "token"}})
        )
        respx_mock.get("/locations/101").mock(
            return_value=Response(
                200, 
                json={
                    "code": True, 
                    "data": {
                        "id": 101, 
                        "name": "Loc 1", 
                        "institution_id": 1,
                        "street_address": "123 St",
                        "map_by_operatory": True,
                        "latitude": 37.77,
                        "longitude": -122.39
                    }
                }
            )
        )
        
        response = test_client.get(
            "/api/v1/nexhealth/locations/101",
            headers={"x-admin-api-key": "test-admin-key"}
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == 101
        assert data["street_address"] == "123 St"
        assert data["map_by_operatory"] is True
        assert data["latitude"] == 37.77

def test_list_appointment_descriptors(test_client, mock_settings):
    with respx.mock(base_url=mock_settings.base_url) as respx_mock:
        respx_mock.post("/authenticates").mock(
            return_value=Response(201, json={"code": True, "data": {"token": "token"}})
        )
        respx_mock.get("/locations/101/appointment_descriptors").mock(
            return_value=Response(
                200, 
                json={
                    "code": True, 
                    "data": [
                        {"id": 500, "name": "Exam", "duration": 30}
                    ]
                }
            )
        )
        
        response = test_client.get(
            "/api/v1/nexhealth/locations/101/appointment_descriptors",
            headers={"x-admin-api-key": "test-admin-key"}
        )
        assert response.status_code == 200
        assert response.json()["data"][0]["name"] == "Exam"
