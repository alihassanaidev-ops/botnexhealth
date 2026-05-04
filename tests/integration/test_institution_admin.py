"""
Integration tests for super-admin institution management endpoints.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import get_current_admin
from src.app.main import app
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.user import User, UserRole


def _latest_audit_entry(entries, action: AuditAction, *, target_resource: str | None = None):
    matches = [
        entry
        for entry in entries
        if entry.action == action
        and (target_resource is None or entry.target_resource == target_resource)
    ]
    assert matches, f"Expected audit entry for {action}"
    return matches[-1]


@pytest.mark.asyncio
async def test_create_institution_with_initial_admin(async_client: AsyncClient):
    """SUPER_ADMIN can create an institution and invite the initial institution admin."""
    payload = {
        "name": "ScaleNexus Dental",
        "slug": "scalenexus-dental",
        "email": "owner@example.com",
        "location_limit": 3,
    }

    mock_super_admin = User(
        id="99999999-9999-9999-9999-999999999999",
        email="super@example.com",
        role=UserRole.SUPER_ADMIN.value,
        institution_id=None,
        is_active=True,
    )
    app.dependency_overrides[get_current_admin] = lambda: mock_super_admin

    with patch("src.app.api.routes.admin_institutions.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.admin_institutions.InstitutionService"
    ) as MockInstitutionService, patch(
        "src.app.api.routes.admin_institutions.UserInviteService"
    ) as MockUserInviteService:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        # Existing user check query result
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = query_result

        mock_service = AsyncMock()
        mock_service.get_by_slug.return_value = None
        mock_service.create.return_value = SimpleNamespace(
            id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            name=payload["name"],
            slug=payload["slug"],
            is_active=True,
            location_limit=payload["location_limit"],
            nexhealth_api_key_encrypted=None,
        )
        MockInstitutionService.return_value = mock_service

        invited_user = User(
            id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            email=payload["email"],
            role=UserRole.INSTITUTION_ADMIN.value,
            institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            is_active=True,
            invite_status="PENDING",
        )
        mock_invite_service = MagicMock()
        mock_invite_service.create_invited_user = AsyncMock(return_value=invited_user)
        MockUserInviteService.return_value = mock_invite_service
        MockUserInviteService.normalize_email.side_effect = lambda email: email.strip().lower()

        try:
            response = await async_client.post("/api/admin/institutions", json=payload)
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == payload["name"]
    assert data["slug"] == payload["slug"]
    assert data["location_limit"] == payload["location_limit"]
    assert data["user"]["email"] == payload["email"]
    assert data["user"]["role"] == UserRole.INSTITUTION_ADMIN.value

    mock_invite_service.create_invited_user.assert_called_once()
    invite_kwargs = mock_invite_service.create_invited_user.call_args.kwargs
    assert invite_kwargs["email"] == payload["email"]
    assert invite_kwargs["institution_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert invite_kwargs["role"] == UserRole.INSTITUTION_ADMIN.value


@pytest.mark.asyncio
async def test_admin_reinvite_institution_user_logs_reinvite_action(
    async_client: AsyncClient,
    audit_log_entries,
):
    """SUPER_ADMIN reinvite should reuse the same user and emit USER_REINVITED."""
    current_admin = User(
        id="99999999-9999-9999-9999-999999999999",
        email="super@example.com",
        role=UserRole.SUPER_ADMIN.value,
        is_active=True,
    )
    target_user = User(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        email="owner@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
    )
    app.dependency_overrides[get_current_admin] = lambda: current_admin

    with patch("src.app.api.routes.admin_institutions.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.admin_institutions.InstitutionService"
    ) as MockInstitutionService, patch(
        "src.app.api.routes.admin_institutions.UserInviteService"
    ) as MockUserInviteService:
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__.return_value = mock_session

        mock_service = AsyncMock()
        mock_service.get_by_slug.return_value = SimpleNamespace(
            id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            slug="scalenexus-dental",
            is_active=True,
        )
        MockInstitutionService.return_value = mock_service
        MockUserInviteService.normalize_email.side_effect = lambda email: email.strip().lower()

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = target_user
        mock_session.execute.return_value = query_result

        mock_invite_service = MagicMock()
        mock_invite_service.reinvite_user = AsyncMock(return_value=target_user)
        MockUserInviteService.return_value = mock_invite_service

        try:
            response = await async_client.post(
                "/api/admin/institutions/scalenexus-dental/reinvite",
                json={"email": "owner@example.com"},
            )
        finally:
            app.dependency_overrides = {}

    assert response.status_code == 200
    assert response.json()["message"] == "Invite re-sent to owner@example.com"
    mock_invite_service.reinvite_user.assert_called_once_with(target_user)
    entry = _latest_audit_entry(
        await audit_log_entries(),
        AuditAction.USER_REINVITED,
        target_resource="user:owner@example.com:reinvite",
    )
    assert entry.outcome == AuditOutcome.SUCCESS
    assert entry.user_id == current_admin.id
    assert entry.institution_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert entry.metadata["old_user_id"] == target_user.id
    assert entry.metadata["new_user_id"] == target_user.id
