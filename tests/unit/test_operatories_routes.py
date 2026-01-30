
import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response
from src.app.main import app
from src.app.dependencies import get_settings

@pytest.fixture
def override_settings(mock_settings):
    app.dependency_overrides[get_settings] = lambda: mock_settings
    mock_settings.admin_api_key = "test-admin-key"
    mock_settings.nexhealth_subdomain = "test-subdomain"
    mock_settings.nexhealth_location_id = 123
    return mock_settings

@pytest.fixture
def test_client(override_settings):
    with TestClient(app) as client:
        yield client

def test_list_operatories(test_client, mock_settings):
    with respx.mock(base_url=mock_settings.base_url) as respx_mock:
        # Mock Auth
        respx_mock.post("/authenticates").mock(
            return_value=Response(201, json={"code": True, "data": {"token": "token"}})
        )
        
        # Mock List Operatories
        expected_operatories = [
            {
                "id": 1,
                "name": "Op 1",
                "location_id": 123,
                "active": True
            },
            {
                "id": 2,
                "name": "Op 2",
                "location_id": 123,
                "active": False
            }
        ]
        
        respx_mock.get("/operatories").mock(
            return_value=Response(
                200, 
                json={
                    "code": True, 
                    "data": expected_operatories
                }
            )
        )
        
        # Call API
        response = test_client.get(
            "/api/v1/nexhealth/operatories",
            headers={"x-admin-api-key": "test-admin-key"},
             # We rely on settings for subdomain/location_id
        )
        
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[0]["name"] == "Op 1"

def test_list_operatories_missing_params(test_client, mock_settings):
    # Unset settings to force error
    mock_settings.nexhealth_subdomain = None
    mock_settings.nexhealth_location_id = None
    
    response = test_client.get(
        "/api/v1/nexhealth/operatories",
        headers={"x-admin-api-key": "test-admin-key"}
    )
    
    assert response.status_code == 400
    assert "Missing subdomain or location_id" in response.json()["detail"]

def test_get_operatory(test_client, mock_settings):
    with respx.mock(base_url=mock_settings.base_url) as respx_mock:
        respx_mock.post("/authenticates").mock(
            return_value=Response(201, json={"code": True, "data": {"token": "token"}})
        )
        
        operatory_id = 999
        respx_mock.get(f"/operatories/{operatory_id}").mock(
            return_value=Response(
                200, 
                json={
                    "code": True, 
                    "data": {
                        "id": operatory_id,
                        "name": "Single Op",
                        "location_id": 123,
                    }
                }
            )
        )
        
        response = test_client.get(
            f"/api/v1/nexhealth/operatories/{operatory_id}",
            headers={"x-admin-api-key": "test-admin-key"}
        )
        
        assert response.status_code == 200
        assert response.json()["data"]["id"] == operatory_id
        assert response.json()["data"]["name"] == "Single Op"

def test_get_operatory_missing_subdomain(test_client, mock_settings):
    mock_settings.nexhealth_subdomain = None
    
    response = test_client.get(
        "/api/v1/nexhealth/operatories/1",
        headers={"x-admin-api-key": "test-admin-key"}
    )
    
    assert response.status_code == 400
    assert "Missing subdomain" in response.json()["detail"]
