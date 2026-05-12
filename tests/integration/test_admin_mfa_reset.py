"""Break-glass admin endpoint for wiping a user's MFA factors.

Required when a user loses every authenticator AND every recovery code
(unlikely but inevitable at scale). Without it the only recovery path
is DB surgery, which leaves no audit row.

Locked down with two gates:
  1. SUPER_ADMIN role required.
  2. Step-up MFA verification by the admin required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import (
    get_current_active_user,
    get_current_super_admin,
)
from src.app.api.routes import auth as auth_routes
from src.app.main import app
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.mfa import MFA_PURPOSE_STEP_UP, MfaTicket
from src.app.services.password_service import PasswordService


@pytest.fixture
def super_admin() -> User:
    return User(
        id="00000000-0000-0000-0000-000000000001",
        email="root@example.com",
        role=UserRole.SUPER_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
        password_hash=PasswordService.hash_password("ValidPass123!"),
    )


@pytest.fixture
def target_user() -> User:
    return User(
        id="11111111-1111-1111-1111-111111111111",
        email="locked-out@clinic.test",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
        password_hash=PasswordService.hash_password("Whatever123!"),
    )


@pytest.fixture
def override_super_admin(super_admin):
    app.dependency_overrides[get_current_super_admin] = lambda: super_admin
    try:
        yield super_admin
    finally:
        app.dependency_overrides.pop(get_current_super_admin, None)


@pytest.fixture
def stub_step_up_for(super_admin):
    """Pretend the admin's step-up ticket validated cleanly."""
    fake = MfaTicket(
        token="step-up-token",
        user_id=str(super_admin.id),
        purpose=MFA_PURPOSE_STEP_UP,
        role=UserRole.SUPER_ADMIN.value,
        institution_id=str(super_admin.institution_id),
        location_id=None,
        audit_request_id="audit-req-1",
        elevated=True,
    )
    with patch.object(
        auth_routes.MfaTicketService,
        "consume_step_up",
        new=AsyncMock(return_value=fake),
    ) as consumed:
        yield consumed


@pytest.fixture
def stub_db_with_target(target_user):
    """get_db_session yields a session that returns target_user from the
    User lookup. The MfaService.wipe_all_factors call is stubbed
    separately per test."""
    @asynccontextmanager
    async def _ctx(*_args, **_kwargs):
        session = AsyncMock()
        result = AsyncMock()
        result.scalar_one_or_none = lambda: target_user
        session.execute = AsyncMock(return_value=result)
        yield session

    with patch.object(auth_routes, "get_db_session", _ctx):
        yield


@pytest.mark.asyncio
async def test_admin_mfa_reset_requires_super_admin(
    async_client: AsyncClient,
    target_user,
):
    """Non-super-admin users (even institution admins) cannot break-glass."""
    non_super = User(
        id="00000000-0000-0000-0000-000000000099",
        email="inst@admin.test",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        is_active=True,
    )
    # Only override get_current_active_user; get_current_super_admin
    # will reject this role with 403.
    app.dependency_overrides[get_current_active_user] = lambda: non_super
    try:
        response = await async_client.post(
            f"/api/auth/admin/users/{target_user.id}/mfa/reset",
            json={"mfa_ticket": "anything"},
        )
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_mfa_reset_requires_step_up(
    async_client: AsyncClient,
    override_super_admin: User,
    target_user,
):
    """Super-admin alone is not enough — the admin must also produce a
    fresh elevated step-up ticket. An invalid ticket → 401."""
    from src.app.services.mfa import MfaTicketInvalid

    with patch.object(
        auth_routes.MfaTicketService,
        "consume_step_up",
        new=AsyncMock(side_effect=MfaTicketInvalid("Invalid")),
    ), patch.object(
        auth_routes.MfaService, "wipe_all_factors", new=AsyncMock(),
    ) as wipe_call:
        response = await async_client.post(
            f"/api/auth/admin/users/{target_user.id}/mfa/reset",
            json={"mfa_ticket": "bogus"},
        )

    assert response.status_code == 401
    wipe_call.assert_not_awaited()  # never touched factor state


@pytest.mark.asyncio
async def test_admin_mfa_reset_wipes_all_factors_and_audits(
    async_client: AsyncClient,
    override_super_admin: User,
    target_user,
    stub_step_up_for,
    stub_db_with_target,
    audit_log_entries,
):
    """The happy path: super-admin + valid step-up → wipe + revoke +
    audit row covering both."""
    with patch.object(
        auth_routes.MfaService,
        "wipe_all_factors",
        new=AsyncMock(return_value={"webauthn": 2, "totp": 1, "recovery_codes": 8}),
    ) as wipe_call, patch.object(
        auth_routes.RefreshTokenService,
        "revoke_all_for_user",
        new=AsyncMock(return_value=3),
    ) as revoke_refresh, patch.object(
        auth_routes.RefreshTokenService,
        "revoke_all_access_tokens_for_user",
        new=AsyncMock(return_value=2),
    ) as revoke_access:
        response = await async_client.post(
            f"/api/auth/admin/users/{target_user.id}/mfa/reset",
            json={"mfa_ticket": "valid-elevated"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == target_user.id
    assert body["removed"] == {"webauthn": 2, "totp": 1, "recovery_codes": 8}
    # Factor wipe AND session revocation both fired against the target.
    wipe_call.assert_awaited_once_with(user_id=target_user.id)
    revoke_refresh.assert_awaited_once_with(target_user.id)
    revoke_access.assert_awaited_once_with(target_user.id)

    # §164.312(b) audit row records who reset whom, what was removed,
    # and how many sessions were terminated.
    entries = await audit_log_entries()
    matches = [
        e for e in entries
        if e.action == AuditAction.MFA_FACTOR_REMOVE
        and e.outcome == AuditOutcome.SUCCESS
        and e.metadata.get("reason") == "admin_break_glass_reset"
    ]
    assert len(matches) == 1
    row = matches[0]
    assert row.target_resource == f"user:{target_user.id}/mfa"
    assert row.metadata["target_user_id"] == target_user.id
    assert row.metadata["removed"] == {"webauthn": 2, "totp": 1, "recovery_codes": 8}
    assert row.metadata["revoked_refresh_tokens"] == 3
    assert row.metadata["revoked_access_tokens"] == 2


@pytest.mark.asyncio
async def test_admin_mfa_reset_404_on_unknown_user(
    async_client: AsyncClient,
    override_super_admin: User,
    stub_step_up_for,
):
    @asynccontextmanager
    async def _ctx_no_user(*_args, **_kwargs):
        session = AsyncMock()
        result = AsyncMock()
        result.scalar_one_or_none = lambda: None
        session.execute = AsyncMock(return_value=result)
        yield session

    with patch.object(auth_routes, "get_db_session", _ctx_no_user), patch.object(
        auth_routes.MfaService, "wipe_all_factors", new=AsyncMock(),
    ) as wipe_call:
        response = await async_client.post(
            "/api/auth/admin/users/99999999-9999-9999-9999-999999999999/mfa/reset",
            json={"mfa_ticket": "valid-elevated"},
        )

    assert response.status_code == 404
    wipe_call.assert_not_awaited()
