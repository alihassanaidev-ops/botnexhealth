from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.api.routes import retell_sms
from src.app.models.user import UserRole


def _user(role: str, *, institution_id: str | None = None, location_id: str | None = None):
    return SimpleNamespace(
        role=role,
        institution_id=institution_id,
        location_id=location_id,
    )


@pytest.mark.asyncio
async def test_superadmin_profile_list_requires_location_scope() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await retell_sms.list_profiles(
            _user(UserRole.SUPER_ADMIN.value),
            location_id=None,
            is_active=None,
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_location_user_cannot_list_another_locations_profiles() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await retell_sms.list_profiles(
            _user(
                UserRole.LOCATION_ADMIN.value,
                institution_id="institution-1",
                location_id="location-1",
            ),
            location_id="location-2",
            is_active=None,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_superadmin_profile_list_includes_fields_needed_by_editor(monkeypatch) -> None:
    now = datetime.now(UTC)
    profile = SimpleNamespace(
        id="profile-1",
        institution_id="institution-1",
        location_id="location-1",
        retell_agent_id="agent_chat",
        agent_version=4,
        display_name="Recall assistant",
        purpose="recall",
        allowed_tools=["lookup_patient"],
        is_active=True,
        config={"mode": "test"},
        created_at=now,
        updated_at=now,
    )

    class _Scalars:
        def all(self):
            return [profile]

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        async def execute(self, _query):
            return _Result()

    class _SessionContext:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return False

    monkeypatch.setattr(retell_sms, "get_db_session", lambda: _SessionContext())

    profiles = await retell_sms.list_profiles(
        _user(UserRole.SUPER_ADMIN.value),
        location_id="location-1",
        is_active=None,
    )

    assert len(profiles) == 1
    assert profiles[0].retell_agent_id == "agent_chat"
    assert profiles[0].agent_version == 4
    assert profiles[0].allowed_tools == ["lookup_patient"]
    assert profiles[0].config == {"mode": "test"}
