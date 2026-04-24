from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.password_service import PasswordService
from src.app.services.refresh_token_service import RefreshTokenService
from src.app.services.user_invite_service import UserInviteService


@pytest.mark.asyncio
async def test_create_invited_user_sets_invite_state_and_sends_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    email_service = MagicMock()
    email_service.send_invite_email = AsyncMock()

    monkeypatch.setattr(
        PasswordService,
        "generate_one_time_token",
        staticmethod(lambda: "plain-invite-token"),
    )

    service = UserInviteService(session, email_service=email_service)

    user = await service.create_invited_user(
        email="NewUser@Example.com",
        role=UserRole.LOCATION_ADMIN.value,
        institution_id="institution-1",
        location_id="location-1",
    )

    assert user.id
    assert user.email == "newuser@example.com"
    assert user.role == UserRole.LOCATION_ADMIN.value
    assert user.institution_id == "institution-1"
    assert user.location_id == "location-1"
    assert user.invite_status == InviteStatus.PENDING.value
    assert user.password_hash is None
    assert user.password_set_at is None
    assert user.invite_token_hash == PasswordService.hash_token("plain-invite-token")
    assert user.invite_expires_at is not None
    assert user.invite_expires_at > datetime.now(timezone.utc)
    assert user.password_reset_token_hash is None
    assert user.password_reset_expires_at is None
    assert user.failed_login_attempts == 0
    assert user.locked_until is None

    session.add.assert_called_once_with(user)
    session.flush.assert_awaited_once()
    email_service.send_invite_email.assert_awaited_once_with(
        email="newuser@example.com",
        token="plain-invite-token",
        redirect_url=None,
    )


@pytest.mark.asyncio
async def test_reinvite_user_resets_local_auth_state_and_revokes_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    session = AsyncMock()
    session.flush = AsyncMock()

    email_service = MagicMock()
    email_service.send_invite_email = AsyncMock()

    revoke_all = AsyncMock(return_value=2)
    revoke_all_access = AsyncMock(return_value=1)
    monkeypatch.setattr(
        RefreshTokenService,
        "revoke_all_for_user",
        revoke_all,
    )
    monkeypatch.setattr(
        RefreshTokenService,
        "revoke_all_access_tokens_for_user",
        revoke_all_access,
    )
    monkeypatch.setattr(
        PasswordService,
        "generate_one_time_token",
        staticmethod(lambda: "rotated-invite-token"),
    )

    user = User(
        id="user-1",
        email="Staff@Clinic.com",
        role=UserRole.STAFF.value,
        institution_id="institution-1",
        location_id="location-1",
        invite_status=InviteStatus.ACCEPTED.value,
        is_active=True,
        password_hash="existing-hash",
        password_set_at=now,
        password_reset_token_hash="old-reset-token",
        password_reset_expires_at=now,
        failed_login_attempts=3,
        locked_until=now,
    )

    service = UserInviteService(session, email_service=email_service)

    result = await service.reinvite_user(user)

    assert result is user
    assert user.email == "staff@clinic.com"
    assert user.invite_status == InviteStatus.PENDING.value
    assert user.password_hash is None
    assert user.password_set_at is None
    assert user.invite_token_hash == PasswordService.hash_token("rotated-invite-token")
    assert user.invite_expires_at is not None
    assert user.password_reset_token_hash is None
    assert user.password_reset_expires_at is None
    assert user.failed_login_attempts == 0
    assert user.locked_until is None

    session.flush.assert_awaited_once()
    email_service.send_invite_email.assert_awaited_once_with(
        email="staff@clinic.com",
        token="rotated-invite-token",
        redirect_url=None,
    )
    revoke_all.assert_awaited_once_with("user-1")
    revoke_all_access.assert_awaited_once_with("user-1")
