from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.models.user import User, UserRole
from src.app.services.institution_service import InstitutionService


def _location() -> SimpleNamespace:
    return SimpleNamespace(id="loc-1", slug="downtown", is_active=True)


def _location_user(email: str, role: str = UserRole.LOCATION_ADMIN.value) -> User:
    return User(
        id="11111111-1111-1111-1111-111111111111",
        email=email,
        role=role,
        institution_id="inst-1",
        location_id="loc-1",
        is_active=True,
    )


@pytest.mark.asyncio
async def test_soft_delete_location_marks_scoped_users_deleted() -> None:
    location = _location()
    location_admin = _location_user("location-admin@example.com")
    staff = _location_user("staff@example.com", role=UserRole.STAFF.value)

    result = MagicMock()
    result.scalars.return_value.all.return_value = [location_admin, staff]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()

    await InstitutionService(session).delete_location(location, hard=False)

    assert location.is_active is False
    assert location_admin.is_active is False
    assert location_admin.deleted_at is not None
    assert staff.is_active is False
    assert staff.deleted_at is not None
    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_hard_delete_location_deletes_only_location_row() -> None:
    location = _location()
    session = AsyncMock()
    session.delete = AsyncMock()

    await InstitutionService(session).delete_location(location, hard=True)

    session.delete.assert_awaited_once_with(location)
    session.execute.assert_not_called()
