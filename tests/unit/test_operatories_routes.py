import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from src.app.main import app
from src.app.config import get_settings, settings as global_settings
from src.app.api.deps import get_current_institution_or_location_user
from src.app.models.user import User, UserRole, InviteStatus
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import UniversalOperatory


@pytest.fixture
def override_dependencies(mock_settings):
    app.dependency_overrides[get_settings] = lambda: mock_settings

    original = {
        "database_url": global_settings.database_url,
        "app_env": global_settings.app_env,
        "jwt_secret": global_settings.jwt_secret,
    }
    global_settings.database_url = None
    global_settings.app_env = "test"
    global_settings.jwt_secret = mock_settings.jwt_secret

    mock_user = User(
        id="00000000-0000-0000-0000-000000000000",
        email="user@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="11111111-1111-1111-1111-111111111111",
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
    )
    app.dependency_overrides[get_current_institution_or_location_user] = lambda: mock_user

    mock_adapter = AsyncMock()
    app.dependency_overrides[get_institution_pms] = lambda: mock_adapter

    try:
        yield mock_adapter
    finally:
        app.dependency_overrides = {}
        for key, value in original.items():
            setattr(global_settings, key, value)


@pytest.fixture
def test_client(override_dependencies):
    with TestClient(app) as client:
        yield client


def test_list_operatories(test_client, override_dependencies):
    mock_adapter = override_dependencies
    mock_adapter.list_operatories = AsyncMock(
        return_value=[
            UniversalOperatory(id="1", source="nexhealth", name="Op 1", is_active=True),
            UniversalOperatory(id="2", source="nexhealth", name="Op 2", is_active=False),
        ]
    )

    response = test_client.get("/api/v1/pms/operatories")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["id"] == "1"
    assert data[0]["name"] == "Op 1"
    mock_adapter.list_operatories.assert_awaited_once()


def test_list_operatories_empty(test_client, override_dependencies):
    mock_adapter = override_dependencies
    mock_adapter.list_operatories = AsyncMock(return_value=[])

    response = test_client.get("/api/v1/pms/operatories")

    assert response.status_code == 200
    assert response.json() == []
    mock_adapter.list_operatories.assert_awaited_once()
