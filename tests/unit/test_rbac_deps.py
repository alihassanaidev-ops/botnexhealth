"""
Unit tests for role-based access dependencies.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.app.api.deps import (
    get_current_institution_admin,
    get_current_institution_or_location_user,
    get_current_location_admin,
    get_current_super_admin,
)
from src.app.models.user import User, UserRole


def _user(role: UserRole, *, location_id: str | None = None) -> User:
    return User(
        id="11111111-1111-1111-1111-111111111111",
        email="test@example.com",
        role=role.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        location_id=location_id,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_super_admin_dependency_allows_only_super_admin():
    allowed = await get_current_super_admin(_user(UserRole.SUPER_ADMIN))
    assert allowed.role == UserRole.SUPER_ADMIN.value

    with pytest.raises(HTTPException) as exc:
        await get_current_super_admin(_user(UserRole.INSTITUTION_ADMIN))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_institution_admin_dependency():
    allowed = await get_current_institution_admin(_user(UserRole.INSTITUTION_ADMIN))
    assert allowed.role == UserRole.INSTITUTION_ADMIN.value

    with pytest.raises(HTTPException):
        await get_current_institution_admin(_user(UserRole.LOCATION_ADMIN, location_id="loc-1"))


@pytest.mark.asyncio
async def test_location_admin_dependency_requires_location_scope():
    allowed = await get_current_location_admin(_user(UserRole.LOCATION_ADMIN, location_id="loc-1"))
    assert allowed.location_id == "loc-1"

    with pytest.raises(HTTPException) as exc:
        await get_current_location_admin(_user(UserRole.LOCATION_ADMIN, location_id=None))
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException):
        await get_current_location_admin(_user(UserRole.STAFF, location_id="loc-1"))


@pytest.mark.asyncio
async def test_institution_or_location_user_allows_three_roles():
    for role in (UserRole.INSTITUTION_ADMIN, UserRole.LOCATION_ADMIN, UserRole.STAFF):
        current = await get_current_institution_or_location_user(
            _user(role, location_id="loc-1" if role != UserRole.INSTITUTION_ADMIN else None)
        )
        assert current.role == role.value

    with pytest.raises(HTTPException):
        await get_current_institution_or_location_user(_user(UserRole.SUPER_ADMIN))
