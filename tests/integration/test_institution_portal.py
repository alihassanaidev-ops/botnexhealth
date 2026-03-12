"""
Integration tests for institution portal endpoints.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import (
    get_current_institution_admin,
    get_current_institution_or_location_admin,
    get_current_institution_or_location_user,
    get_current_location_admin,
)
from src.app.main import app
from src.app.models.user import User, UserRole, InviteStatus


@pytest.mark.asyncio
async def test_get_my_institution_context_success(async_client: AsyncClient):
    """Institution users can read their non-sensitive institution context."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
    )

    app.dependency_overrides[get_current_institution_or_location_user] = lambda: mock_user

    with patch("src.app.api.routes.institution_portal.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.institution_portal.InstitutionService"
    ) as MockInstitutionService:
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        mock_service = AsyncMock()
        mock_service.get_by_id.return_value = SimpleNamespace(
            id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            name="Lawendy Dental Group",
            slug="lawendy-group",
        )
        MockInstitutionService.return_value = mock_service

        try:
            response = await async_client.get("/institution/me")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert data["name"] == "Lawendy Dental Group"
    assert data["slug"] == "lawendy-group"
    assert data["role"] == UserRole.INSTITUTION_ADMIN.value
    # Non-sensitive response contract: no integration booleans/secrets
    assert "has_nexhealth_key" not in data
    assert "has_retell_secret" not in data


@pytest.mark.asyncio
async def test_get_my_institution_context_requires_institution(async_client: AsyncClient):
    """Users without institution assignment are rejected."""
    mock_user = User(
        id="22222222-2222-2222-2222-222222222222",
        email="orphan@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id=None,
        is_active=True,
    )

    app.dependency_overrides[get_current_institution_or_location_user] = lambda: mock_user
    try:
        response = await async_client.get("/institution/me")
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 400
    assert response.json()["detail"] == "User is not associated with an institution"


@pytest.mark.asyncio
async def test_list_institution_users_success(async_client: AsyncClient):
    """Institution admin can list users across institution locations."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
    )
    app.dependency_overrides[get_current_institution_admin] = lambda: mock_user

    user_1 = User(
        id="22222222-2222-2222-2222-222222222222",
        email="ia@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
    )
    user_2 = User(
        id="33333333-3333-3333-3333-333333333333",
        email="la@clinic.com",
        role=UserRole.LOCATION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        location_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
    )
    loc = SimpleNamespace(id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", name="Downtown Clinic")

    with patch("src.app.api.routes.institution_portal.get_db_session") as mock_get_db:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        users_result = MagicMock()
        users_result.scalars.return_value.all.return_value = [user_1, user_2]
        locs_result = MagicMock()
        locs_result.scalars.return_value.all.return_value = [loc]
        mock_session.execute.side_effect = [users_result, locs_result]

        try:
            response = await async_client.get("/institution/users")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["email"] == "ia@clinic.com"
    assert payload[0]["location_name"] is None
    assert payload[1]["email"] == "la@clinic.com"
    assert payload[1]["location_name"] == "Downtown Clinic"


@pytest.mark.asyncio
async def test_invite_institution_user_rejects_staff_role(async_client: AsyncClient):
    """Institution admins can invite INSTITUTION_ADMIN and LOCATION_ADMIN only."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
    )
    app.dependency_overrides[get_current_institution_admin] = lambda: mock_user

    with patch("src.app.api.routes.institution_portal.get_db_session") as mock_get_db:
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = existing_result

        try:
            response = await async_client.post(
                "/institution/users/invite",
                json={"email": "staff@clinic.com", "role": "STAFF"},
            )
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 422
    assert "Invalid role 'STAFF'" in response.json()["detail"]


@pytest.mark.asyncio
async def test_invite_institution_user_location_admin_success(async_client: AsyncClient):
    """Institution admin can invite LOCATION_ADMIN with explicit location assignment."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
    )
    app.dependency_overrides[get_current_institution_admin] = lambda: mock_user

    with patch("src.app.api.routes.institution_portal.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.institution_portal.SupabaseService"
    ) as MockSupabaseService:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        actor_result = MagicMock()
        actor_result.scalar_one_or_none.return_value = mock_user
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        location_result = MagicMock()
        location_result.scalar_one_or_none.return_value = SimpleNamespace(
            id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            slug="downtown",
            institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        mock_session.execute.side_effect = [actor_result, existing_result, location_result]

        mock_supabase = MagicMock()
        mock_response = MagicMock()
        mock_response.user.id = "44444444-4444-4444-4444-444444444444"
        mock_supabase.invite_user.return_value = mock_response
        MockSupabaseService.return_value = mock_supabase

        try:
            response = await async_client.post(
                "/institution/users/invite",
                json={
                    "email": "new-location-admin@clinic.com",
                    "role": "LOCATION_ADMIN",
                    "location_slug": "downtown",
                },
            )
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 201
    payload = response.json()
    assert payload["user_id"] == "44444444-4444-4444-4444-444444444444"
    mock_supabase.invite_user.assert_called_once()
    invite_kwargs = mock_supabase.invite_user.call_args.kwargs
    assert invite_kwargs["email"] == "new-location-admin@clinic.com"
    assert invite_kwargs["role"] == UserRole.LOCATION_ADMIN.value
    assert invite_kwargs["location_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.mark.asyncio
async def test_location_admin_can_invite_staff_for_own_location(async_client: AsyncClient):
    """Location admins can invite STAFF only for their assigned location."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="locadmin@clinic.com",
        role=UserRole.LOCATION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        location_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
    )
    app.dependency_overrides[get_current_location_admin] = lambda: mock_user

    with patch("src.app.api.routes.institution_portal.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.institution_portal.SupabaseService"
    ) as MockSupabaseService, patch(
        "src.app.api.routes.institution_portal.InstitutionService"
    ) as MockInstitutionService:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        mock_svc = AsyncMock()
        mock_svc.get_location_by_slug.return_value = SimpleNamespace(
            id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            slug="downtown",
            institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        MockInstitutionService.return_value = mock_svc

        actor_result = MagicMock()
        actor_result.scalar_one_or_none.return_value = mock_user
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        mock_session.execute.side_effect = [actor_result, existing_result]

        mock_supabase = MagicMock()
        mock_response = MagicMock()
        mock_response.user.id = "44444444-4444-4444-4444-444444444444"
        mock_supabase.invite_user.return_value = mock_response
        MockSupabaseService.return_value = mock_supabase

        try:
            response = await async_client.post(
                "/institution/locations/downtown/invite-staff",
                json={"email": "staff@clinic.com"},
            )
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 201
    mock_supabase.invite_user.assert_called_once()
    invite_kwargs = mock_supabase.invite_user.call_args.kwargs
    assert invite_kwargs["email"] == "staff@clinic.com"
    assert invite_kwargs["role"] == UserRole.STAFF.value
    assert invite_kwargs["location_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.mark.asyncio
async def test_deactivate_institution_user_rejects_self(async_client: AsyncClient):
    """Institution admin cannot deactivate their own account."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
    )
    app.dependency_overrides[get_current_institution_admin] = lambda: mock_user
    try:
        response = await async_client.post("/institution/users/11111111-1111-1111-1111-111111111111/deactivate")
    finally:
        app.dependency_overrides = {}

    assert response.status_code == 400
    assert response.json()["detail"] == "Cannot deactivate your own account"


@pytest.mark.asyncio
async def test_reinvite_institution_user_success(async_client: AsyncClient):
    """Reinvite replaces auth account and returns new user UUID."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
    )
    app.dependency_overrides[get_current_institution_admin] = lambda: mock_user

    target_user = User(
        id="22222222-2222-2222-2222-222222222222",
        email="staff@clinic.com",
        role=UserRole.STAFF.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        location_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
    )

    with patch("src.app.api.routes.institution_portal.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.institution_portal.SupabaseService"
    ) as MockSupabaseService:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.delete = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        actor_result = MagicMock()
        actor_result.scalar_one_or_none.return_value = mock_user
        target_result = MagicMock()
        target_result.scalar_one_or_none.return_value = target_user
        mock_session.execute.side_effect = [actor_result, target_result]

        mock_supabase = MagicMock()
        mock_response = MagicMock()
        mock_response.user.id = "33333333-3333-3333-3333-333333333333"
        mock_supabase.invite_user.return_value = mock_response
        MockSupabaseService.return_value = mock_supabase

        try:
            response = await async_client.post("/institution/users/22222222-2222-2222-2222-222222222222/reinvite")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "33333333-3333-3333-3333-333333333333"

    mock_supabase.delete_user.assert_called_once_with("22222222-2222-2222-2222-222222222222")
    mock_supabase.invite_user.assert_called_once()
    invite_kwargs = mock_supabase.invite_user.call_args.kwargs
    assert invite_kwargs["email"] == "staff@clinic.com"
    assert invite_kwargs["role"] == UserRole.STAFF.value
    assert invite_kwargs["location_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.mark.asyncio
async def test_list_transfer_numbers_institution_admin_success(async_client: AsyncClient):
    """Institution admin can list transfer numbers across locations."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@clinic.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
    )
    app.dependency_overrides[get_current_institution_or_location_user] = lambda: mock_user

    loc_1 = SimpleNamespace(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        name="Downtown Clinic",
        slug="downtown",
    )
    loc_2 = SimpleNamespace(
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        name="Uptown Clinic",
        slug="uptown",
    )
    tn_1 = SimpleNamespace(
        id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        location_id=loc_1.id,
        institution_id=mock_user.institution_id,
        phone_number="+15551230001",
        department="Reception",
    )
    tn_2 = SimpleNamespace(
        id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        location_id=loc_2.id,
        institution_id=mock_user.institution_id,
        phone_number="+15551230002",
        department="Billing",
    )

    with patch("src.app.api.routes.institution_portal.get_db_session") as mock_get_db:
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        rows_result = MagicMock()
        rows_result.all.return_value = [(tn_1, loc_1), (tn_2, loc_2)]
        mock_session.execute.return_value = rows_result

        try:
            response = await async_client.get("/institution/transfer-numbers")
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["phone_number"] == "+15551230001"
    assert payload[0]["department"] == "Reception"
    assert payload[0]["location_name"] == "Downtown Clinic"
    assert payload[1]["phone_number"] == "+15551230002"
    assert payload[1]["department"] == "Billing"
    assert payload[1]["location_slug"] == "uptown"


@pytest.mark.asyncio
async def test_create_transfer_number_location_admin_success(async_client: AsyncClient):
    """Location admin can create transfer numbers for their own location."""
    mock_user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="locadmin@clinic.com",
        role=UserRole.LOCATION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        location_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
    )
    app.dependency_overrides[get_current_institution_or_location_admin] = lambda: mock_user

    loc = SimpleNamespace(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        name="Downtown Clinic",
        slug="downtown",
        institution_id=mock_user.institution_id,
    )

    with patch("src.app.api.routes.institution_portal.get_db_session") as mock_get_db:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        location_result = MagicMock()
        location_result.scalar_one_or_none.return_value = loc
        mock_session.execute.return_value = location_result

        try:
            response = await async_client.post(
                "/institution/locations/downtown/transfer-numbers",
                json={"phone_number": "+15551230099", "department": "Front Desk"},
            )
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 201
    payload = response.json()
    assert payload["phone_number"] == "+15551230099"
    assert payload["department"] == "Front Desk"
    assert payload["location_slug"] == "downtown"
