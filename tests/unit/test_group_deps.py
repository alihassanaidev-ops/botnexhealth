"""Unit tests for the GROUP_ADMIN dependency.

The route-level RBAC matrix (``test_rbac_route_matrix``) already asserts that
``get_current_group_admin`` accepts GROUP_ADMIN and rejects every other role on
the /group/* endpoints. These tests cover the branch the matrix doesn't: a
GROUP_ADMIN with no ``group_id`` must be rejected (fail closed).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.app.api.deps import get_current_group_admin
from src.app.models.user import User, UserRole


def _user(role: UserRole, *, group_id: str | None) -> User:
    return User(
        id="11111111-1111-1111-1111-111111111111",
        email="u@example.com",
        role=role.value,
        institution_id=None,
        location_id=None,
        group_id=group_id,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_group_admin_with_group_passes() -> None:
    user = _user(UserRole.GROUP_ADMIN, group_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    assert await get_current_group_admin(user) is user


@pytest.mark.asyncio
async def test_group_admin_without_group_is_rejected() -> None:
    user = _user(UserRole.GROUP_ADMIN, group_id=None)
    with pytest.raises(HTTPException) as exc:
        await get_current_group_admin(user)
    assert exc.value.status_code == 403
    assert "group" in exc.value.detail.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        UserRole.SUPER_ADMIN,
        UserRole.INSTITUTION_ADMIN,
        UserRole.LOCATION_ADMIN,
        UserRole.STAFF,
    ],
)
async def test_non_group_roles_are_rejected(role: UserRole) -> None:
    # Even with a group_id set, a non-GROUP_ADMIN role must not pass.
    user = _user(role, group_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    with pytest.raises(HTTPException) as exc:
        await get_current_group_admin(user)
    assert exc.value.status_code == 403
