
import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response
from src.app.main import app
from src.app.config import get_settings, settings as global_settings
from src.app.api.deps import get_current_admin
from src.app.models.user import User, UserRole, InviteStatus

@pytest.fixture
def override_settings(mock_settings):
    app.dependency_overrides[get_settings] = lambda: mock_settings

    # Patch global settings used by the NexHealth client + app lifespan.
    original = {
        "nexhealth_api_key": global_settings.nexhealth_api_key,
        "nexhealth_base_url": global_settings.nexhealth_base_url,
        "nexhealth_api_version": global_settings.nexhealth_api_version,
        "nexhealth_accept": global_settings.nexhealth_accept,
        "nexhealth_subdomain": global_settings.nexhealth_subdomain,
        "nexhealth_location_id": global_settings.nexhealth_location_id,
        "database_url": global_settings.database_url,
        "app_env": global_settings.app_env,
        "jwt_secret": global_settings.jwt_secret,
    }

    global_settings.nexhealth_api_key = mock_settings.nexhealth_api_key
    global_settings.nexhealth_base_url = mock_settings.nexhealth_base_url
    global_settings.nexhealth_api_version = mock_settings.nexhealth_api_version
    global_settings.nexhealth_accept = mock_settings.nexhealth_accept
    global_settings.nexhealth_subdomain = mock_settings.nexhealth_subdomain
    global_settings.nexhealth_location_id = mock_settings.nexhealth_location_id
    global_settings.database_url = None
    global_settings.app_env = "test"
    global_settings.jwt_secret = mock_settings.jwt_secret

    mock_user = User(
        id="00000000-0000-0000-0000-000000000000",
        email="admin@example.com",
        role=UserRole.SUPER_ADMIN.value,
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
    )
    app.dependency_overrides[get_current_admin] = lambda: mock_user

    try:
        yield mock_settings
    finally:
        app.dependency_overrides = {}
        for key, value in original.items():
            setattr(global_settings, key, value)

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
            headers={}
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
            headers={}
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
            headers={}
        )
        assert response.status_code == 200
        assert response.json()["data"][0]["name"] == "Exam"
