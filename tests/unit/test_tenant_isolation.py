"""Cross-tenant isolation tests.

These cover the *scope* checks that sit below the role checks. Role-based
access is verified in `test_rbac_route_matrix.py`; this file checks that a
user who is *role-allowed* still cannot reach a different tenant's data.

Two enforcement helpers in production code:

1. `institution_portal._assert_location_scope(user, location_id)` — blocks
   only LOCATION_ADMIN with a mismatched `location_id` (used by 10 routes).
   Notably does NOT block STAFF — see test_assert_location_scope_does_not_block_staff.

2. `pms.factory.get_institution_pms` — blocks both LOCATION_ADMIN and STAFF
   when the request's location (query or path) doesn't match the token's
   location.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.app.api.routes.institution_portal import _assert_location_scope
from src.app.models.user import User, UserRole
from src.app.pms.factory import get_institution_pms


_INST_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_INST_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_LOC_A = "11111111-1111-1111-1111-111111111111"
_LOC_B = "22222222-2222-2222-2222-222222222222"


def _user(role: UserRole, *, institution_id: str | None = _INST_A, location_id: str | None = None) -> User:
    return User(
        id="user-1",
        email=f"{role.value.lower()}@example.com",
        role=role.value,
        institution_id=None if role == UserRole.SUPER_ADMIN else institution_id,
        location_id=location_id,
        is_active=True,
    )


# =============================================================================
# institution_portal._assert_location_scope
# =============================================================================

def test_assert_location_scope_blocks_location_admin_in_other_location():
    user = _user(UserRole.LOCATION_ADMIN, location_id=_LOC_A)
    with pytest.raises(HTTPException) as exc:
        _assert_location_scope(user, _LOC_B)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Not authorized for this location"


def test_assert_location_scope_allows_location_admin_in_own_location():
    user = _user(UserRole.LOCATION_ADMIN, location_id=_LOC_A)
    _assert_location_scope(user, _LOC_A)  # no raise


def test_assert_location_scope_allows_institution_admin_anywhere():
    """INSTITUTION_ADMIN spans all locations within their institution by design."""
    user = _user(UserRole.INSTITUTION_ADMIN)
    _assert_location_scope(user, _LOC_A)  # no raise
    _assert_location_scope(user, _LOC_B)  # no raise


def test_assert_location_scope_does_not_block_staff() -> None:
    """KNOWN GAP: helper only restricts LOCATION_ADMIN.

    STAFF passes through, so any read endpoint that admits STAFF and uses
    only this helper for scoping will leak across-location reads to STAFF.
    Verify this is the actual current behavior so we know which routes need
    a tighter check before they handle STAFF requests.
    """
    user = _user(UserRole.STAFF, location_id=_LOC_A)
    _assert_location_scope(user, _LOC_B)  # no raise — current behavior


def test_assert_location_scope_allows_super_admin_anywhere():
    user = _user(UserRole.SUPER_ADMIN, institution_id=None)
    _assert_location_scope(user, _LOC_A)  # no raise
    _assert_location_scope(user, _LOC_B)  # no raise


# =============================================================================
# pms.factory.get_institution_pms — cross-location guard
# =============================================================================

def _request_with(path_location_id: str | None = None):
    request = MagicMock()
    request.path_params = {}
    if path_location_id is not None:
        request.path_params["location_id"] = path_location_id
    request.state = MagicMock()
    return request


@pytest.mark.asyncio
async def test_pms_factory_rejects_location_admin_query_for_other_location():
    user = _user(UserRole.LOCATION_ADMIN, location_id=_LOC_A)
    with pytest.raises(HTTPException) as exc:
        await get_institution_pms(
            request=_request_with(),
            current_user=user,
            loc_id=_LOC_B,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "Not authorized for this location"


@pytest.mark.asyncio
async def test_pms_factory_rejects_location_admin_path_for_other_location():
    user = _user(UserRole.LOCATION_ADMIN, location_id=_LOC_A)
    with pytest.raises(HTTPException) as exc:
        await get_institution_pms(
            request=_request_with(path_location_id=_LOC_B),
            current_user=user,
            loc_id=None,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_pms_factory_rejects_staff_query_for_other_location():
    """Unlike _assert_location_scope, the PMS factory blocks STAFF too."""
    user = _user(UserRole.STAFF, location_id=_LOC_A)
    with pytest.raises(HTTPException) as exc:
        await get_institution_pms(
            request=_request_with(),
            current_user=user,
            loc_id=_LOC_B,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_pms_factory_rejects_staff_without_location_assignment():
    user = _user(UserRole.STAFF, location_id=None)
    with pytest.raises(HTTPException) as exc:
        await get_institution_pms(
            request=_request_with(),
            current_user=user,
            loc_id=None,
        )
    assert exc.value.status_code == 403
    assert "location assignment" in exc.value.detail


@pytest.mark.asyncio
async def test_pms_factory_rejects_user_with_no_institution():
    user = _user(UserRole.INSTITUTION_ADMIN, institution_id=None)
    with pytest.raises(HTTPException) as exc:
        await get_institution_pms(
            request=_request_with(),
            current_user=user,
            loc_id=None,
        )
    assert exc.value.status_code == 400


# =============================================================================
# institution_portal route — sanity that DB scoping rejects cross-institution
# =============================================================================

@pytest.mark.asyncio
async def test_get_location_operating_hours_rejects_other_institution_slug():
    """Slug lookup is filtered by current_user.institution_id, so a slug
    belonging to a DIFFERENT institution should return 404 (location not found),
    not leak the location's data."""
    from httpx import AsyncClient

    from src.app.api import deps as auth_deps
    from src.app.main import app

    user = _user(UserRole.INSTITUTION_ADMIN, institution_id=_INST_A)
    app.dependency_overrides[auth_deps.get_current_institution_or_location_user] = lambda: user

    mock_session = AsyncMock()
    # InstitutionService.get_location_by_slug returns a location belonging to
    # institution B — the route's institution_id check should reject it.
    foreign_location = MagicMock(id=_LOC_B, institution_id=_INST_B)

    from src.app.api.routes import institution_portal as portal_mod

    fake_service = MagicMock()
    fake_service.get_location_by_slug = AsyncMock(return_value=foreign_location)

    from unittest.mock import patch

    try:
        async with AsyncClient(
            transport=__import__("httpx").ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            with patch.object(portal_mod, "get_db_session") as mock_get_db, patch.object(
                portal_mod, "InstitutionService", return_value=fake_service
            ), patch.object(
                portal_mod,
                "ensure_invite_cooldown",
                new=AsyncMock(return_value=user),
            ):
                mock_get_db.return_value.__aenter__.return_value = mock_session
                response = await client.get(
                    "/api/institution/locations/foreign-loc/operating-hours"
                )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 404
    assert response.json()["detail"] == "Location not found"


@pytest.mark.asyncio
async def test_get_location_operating_hours_rejects_location_admin_for_other_location():
    """A LOCATION_ADMIN of location A hitting location B's operating-hours
    must be rejected by _assert_location_scope after the institution check passes."""
    from httpx import AsyncClient

    from src.app.api import deps as auth_deps
    from src.app.main import app

    user = _user(UserRole.LOCATION_ADMIN, institution_id=_INST_A, location_id=_LOC_A)
    app.dependency_overrides[auth_deps.get_current_institution_or_location_user] = lambda: user

    mock_session = AsyncMock()
    # Location belongs to the same institution but is location B (not user's location_id)
    same_inst_other_location = MagicMock(id=_LOC_B, institution_id=_INST_A)

    from src.app.api.routes import institution_portal as portal_mod
    from unittest.mock import patch

    fake_service = MagicMock()
    fake_service.get_location_by_slug = AsyncMock(return_value=same_inst_other_location)

    try:
        async with AsyncClient(
            transport=__import__("httpx").ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            with patch.object(portal_mod, "get_db_session") as mock_get_db, patch.object(
                portal_mod, "InstitutionService", return_value=fake_service
            ), patch.object(
                portal_mod,
                "ensure_invite_cooldown",
                new=AsyncMock(return_value=user),
            ):
                mock_get_db.return_value.__aenter__.return_value = mock_session
                response = await client.get(
                    "/api/institution/locations/other-loc/operating-hours"
                )
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized for this location"
