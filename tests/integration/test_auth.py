"""
Integration tests for Admin Authentication.

Note: The login endpoint requires database access. These tests verify route
accessibility and correct error handling without a database.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type
from httpx import AsyncClient

from src.app.config import settings
from src.app.api.deps import get_current_admin
from src.app.main import app
from src.app.models.audit_log import AuditAction
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.password_service import PasswordService


@pytest.mark.asyncio
async def test_users_me_requires_auth(async_client: AsyncClient):
    """Test that /auth/users/me requires authentication (no DB needed)."""
    response = await async_client.get("/api/auth/users/me")
    assert response.status_code == 401
    data = response.json()
    assert data["detail"] == "Not authenticated"


@pytest.mark.asyncio
async def test_token_endpoint_validation(async_client: AsyncClient):
    """Test that /api/auth/token returns 422 with missing form data."""
    # Send completely empty body - should fail validation before hitting DB
    response = await async_client.post("/api/auth/token")
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_local_login_success(async_client: AsyncClient):
    password = "ValidPass123!"
    user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@example.com",
        role=UserRole.SUPER_ADMIN.value,
        institution_id="22222222-2222-2222-2222-222222222222",
        location_id="33333333-3333-3333-3333-333333333333",
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
        password_hash=PasswordService.hash_password(password),
    )

    mock_session = AsyncMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = user
    mock_session.execute.return_value = query_result

    with patch("src.app.api.routes.auth.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.auth.RefreshTokenService.issue_token",
        new=AsyncMock(return_value="refresh-token-123"),
    ), patch(
        "src.app.api.routes.auth.RefreshTokenService.register_access_token",
        new=AsyncMock(),
    ), patch(
        "src.app.api.routes.auth.log_audit_background"
    ) as mock_log_audit:
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": password},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert "refresh_token" not in data
    set_cookie = response.headers.get("set-cookie", "")
    cookie_lower = set_cookie.lower()
    assert "refresh_token=refresh-token-123" in cookie_lower
    assert "httponly" in cookie_lower
    assert "samesite=strict" in cookie_lower
    assert "secure" in cookie_lower
    assert "path=/api/auth" in cookie_lower
    assert response.cookies.get("refresh_token") == "refresh-token-123"
    assert mock_log_audit.call_args.kwargs["user_id"] == user.id
    assert mock_log_audit.call_args.kwargs["location_id"] == user.location_id


@pytest.mark.asyncio
async def test_local_login_rehashes_outdated_argon2_parameters(async_client: AsyncClient):
    password = "ValidPass123!"
    weak_hash = PasswordHasher(
        time_cost=1,
        memory_cost=8_192,
        parallelism=1,
        hash_len=16,
        salt_len=8,
        type=Type.ID,
    ).hash(password)
    user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@example.com",
        role=UserRole.SUPER_ADMIN.value,
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
        password_hash=weak_hash,
    )

    mock_session = AsyncMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = user
    mock_session.execute.return_value = query_result

    with patch("src.app.api.routes.auth.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.auth.RefreshTokenService.issue_token",
        new=AsyncMock(return_value="refresh-token-123"),
    ), patch(
        "src.app.api.routes.auth.RefreshTokenService.register_access_token",
        new=AsyncMock(),
    ), patch(
        "src.app.api.routes.auth.log_audit_background"
    ):
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": password},
        )

    assert response.status_code == 200
    assert user.password_hash != weak_hash
    assert user.password_hash.startswith("$argon2id$")
    assert PasswordService.needs_rehash(user.password_hash) is False


@pytest.mark.asyncio
async def test_forgot_password_sends_generic_success(async_client: AsyncClient):
    user = User(
        id="22222222-2222-2222-2222-222222222222",
        email="user@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="33333333-3333-3333-3333-333333333333",
        location_id="44444444-4444-4444-4444-444444444444",
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
    )

    mock_session = AsyncMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = user
    mock_session.execute.return_value = query_result

    mock_email_service = MagicMock()
    mock_email_service.send_password_reset_email = AsyncMock()

    with patch("src.app.api.routes.auth.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.auth.AuthEmailService", return_value=mock_email_service
    ), patch("src.app.api.routes.auth.log_audit_background") as mock_log_audit:
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/forgot-password",
            json={
                "email": "user@example.com",
                "redirect_url": "https://dashboard.example.com/set-password",
            },
        )

    assert response.status_code == 200
    assert response.json()["message"].startswith("If an account exists")
    assert user.password_reset_token_hash is not None
    assert user.password_reset_expires_at is not None
    mock_email_service.resolve_redirect_url.assert_called_once()
    mock_email_service.send_password_reset_email.assert_awaited_once()
    assert mock_log_audit.call_args.kwargs["user_id"] == user.id
    assert mock_log_audit.call_args.kwargs["location_id"] == user.location_id


@pytest.mark.asyncio
async def test_forgot_password_rejects_unapproved_redirect(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "auth_frontend_base_url", "https://dashboard.example.com")
    monkeypatch.setattr(settings, "auth_redirect_allowed_hosts", "")

    response = await async_client.post(
        "/api/auth/forgot-password",
        json={
            "email": "user@example.com",
            "redirect_url": "https://evil.example.com/set-password",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Redirect URL host is not allowed"


@pytest.mark.asyncio
async def test_reset_password_audit_logs_user_id(async_client: AsyncClient):
    reset_token = PasswordService.generate_one_time_token()
    user = User(
        id="99999999-9999-9999-9999-999999999999",
        email="reset@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        location_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
        password_reset_token_hash=PasswordService.hash_token(reset_token),
        password_reset_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    mock_session = AsyncMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = user
    mock_session.execute.return_value = query_result

    with patch("src.app.api.routes.auth.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.auth.RefreshTokenService.revoke_all_for_user",
        new=AsyncMock(return_value=0),
    ), patch(
        "src.app.api.routes.auth.RefreshTokenService.revoke_all_access_tokens_for_user",
        new=AsyncMock(return_value=0),
    ), patch(
        "src.app.api.routes.auth.RefreshTokenService.issue_token",
        new=AsyncMock(return_value="refresh-token-reset"),
    ), patch(
        "src.app.api.routes.auth.RefreshTokenService.register_access_token",
        new=AsyncMock(),
    ), patch(
        "src.app.api.routes.auth.log_audit_background"
    ) as mock_log_audit:
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/reset-password",
            json={"token": reset_token, "password": "NewValidPass123!"},
        )

    assert response.status_code == 200
    assert user.password_reset_token_hash is None
    assert user.password_reset_expires_at is None
    assert mock_log_audit.call_args.kwargs["action"] == AuditAction.PASSWORD_RESET_COMPLETE
    assert mock_log_audit.call_args.kwargs["user_id"] == user.id
    assert mock_log_audit.call_args.kwargs["location_id"] == user.location_id


@pytest.mark.asyncio
async def test_set_password_consumes_invite_token(async_client: AsyncClient):
    invite_token = PasswordService.generate_one_time_token()
    user = User(
        id="44444444-4444-4444-4444-444444444444",
        email="invitee@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="55555555-5555-5555-5555-555555555555",
        location_id="66666666-6666-6666-6666-666666666666",
        is_active=True,
        invite_status=InviteStatus.PENDING.value,
        invite_token_hash=PasswordService.hash_token(invite_token),
        invite_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    mock_session = AsyncMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = user
    mock_session.execute.return_value = query_result

    with patch("src.app.api.routes.auth.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.auth.RefreshTokenService.revoke_all_for_user",
        new=AsyncMock(return_value=0),
    ), patch(
        "src.app.api.routes.auth.RefreshTokenService.revoke_all_access_tokens_for_user",
        new=AsyncMock(return_value=0),
    ), patch(
        "src.app.api.routes.auth.RefreshTokenService.issue_token",
        new=AsyncMock(return_value="refresh-token-456"),
    ), patch(
        "src.app.api.routes.auth.RefreshTokenService.register_access_token",
        new=AsyncMock(),
    ), patch(
        "src.app.api.routes.auth.log_audit_background"
    ) as mock_log_audit:
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/set-password",
            json={"token": invite_token, "password": "NewValidPass123!"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["access_token"]
    assert "refresh_token" not in data
    assert response.cookies.get("refresh_token") == "refresh-token-456"
    assert user.invite_status == InviteStatus.ACCEPTED.value
    assert user.invite_token_hash is None
    assert user.invite_expires_at is None
    assert user.password_hash is not None
    assert PasswordService.verify_password("NewValidPass123!", user.password_hash) is True
    assert mock_log_audit.call_args.kwargs["user_id"] == user.id
    assert mock_log_audit.call_args.kwargs["location_id"] == user.location_id


@pytest.mark.asyncio
async def test_refresh_session_rotates_token(async_client: AsyncClient):
    user = User(
        id="66666666-6666-6666-6666-666666666666",
        email="refresh@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="77777777-7777-7777-7777-777777777777",
        location_id="88888888-8888-8888-8888-888888888888",
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
    )

    mock_session = AsyncMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = user
    mock_session.execute.return_value = query_result

    with patch("src.app.api.routes.auth.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.auth.RefreshTokenService.get_user_id_for_token",
        new=AsyncMock(return_value=user.id),
    ), patch(
        "src.app.api.routes.auth.RefreshTokenService.rotate_token",
        new=AsyncMock(return_value="rotated-refresh-token"),
    ), patch(
        "src.app.api.routes.auth.RefreshTokenService.register_access_token",
        new=AsyncMock(),
    ), patch("src.app.api.routes.auth.log_audit_background") as mock_log_audit:
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/refresh",
            cookies={"refresh_token": "refresh-token-abc"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["access_token"]
    assert "refresh_token" not in data
    assert response.cookies.get("refresh_token") == "rotated-refresh-token"
    assert mock_log_audit.call_args.kwargs["user_id"] == user.id
    assert mock_log_audit.call_args.kwargs["location_id"] == user.location_id


@pytest.mark.asyncio
async def test_refresh_session_without_cookie_returns_401(async_client: AsyncClient):
    response = await async_client.post("/api/auth/refresh")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired refresh token"


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(async_client: AsyncClient):
    with patch(
        "src.app.api.routes.auth.RefreshTokenService.revoke_token",
        new=AsyncMock(return_value="88888888-8888-8888-8888-888888888888"),
    ) as revoke_refresh, patch(
        "src.app.api.routes.auth.RefreshTokenService.revoke_access_token_jti",
        new=AsyncMock(),
    ) as revoke_access, patch(
        "src.app.api.routes.auth.AuthService.decode_access_token",
        return_value={"sub": "88888888-8888-8888-8888-888888888888", "jti": "jti-123", "exp": 4102444800},
    ), patch(
        "src.app.api.routes.auth.AuthService.remaining_ttl_seconds",
        return_value=900,
    ), patch("src.app.api.routes.auth.log_audit_background") as mock_log_audit:
        response = await async_client.post(
            "/api/auth/logout",
            cookies={"refresh_token": "refresh-token-logout"},
            headers={"Authorization": "Bearer access-token-logout"},
        )

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully"}
    revoke_refresh.assert_awaited_once_with("refresh-token-logout")
    revoke_access.assert_awaited_once_with(
        "jti-123",
        user_id="88888888-8888-8888-8888-888888888888",
        ttl_seconds=900,
    )

    set_cookie = response.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert 'Max-Age=0' in set_cookie or 'expires=' in set_cookie.lower()
    assert mock_log_audit.call_args.kwargs["user_id"] == "88888888-8888-8888-8888-888888888888"


@pytest.mark.asyncio
async def test_logout_without_cookie_is_idempotent(async_client: AsyncClient):
    with patch(
        "src.app.api.routes.auth.RefreshTokenService.revoke_token",
        new=AsyncMock(),
    ) as revoke_refresh, patch(
        "src.app.api.routes.auth.RefreshTokenService.revoke_access_token_jti",
        new=AsyncMock(),
    ), patch("src.app.api.routes.auth.log_audit_background"):
        response = await async_client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully"}
    revoke_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_unlock_user_account_audit_logs_acting_admin_id(async_client: AsyncClient):
    admin = User(
        id="aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa",
        email="admin@example.com",
        role=UserRole.SUPER_ADMIN.value,
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
    )
    target_user = User(
        id="bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb",
        email="locked@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="cccccccc-3333-3333-3333-cccccccccccc",
        location_id="dddddddd-4444-4444-4444-dddddddddddd",
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
        failed_login_attempts=5,
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=10),
    )

    mock_session = AsyncMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = target_user
    mock_session.execute.return_value = query_result

    with patch("src.app.api.routes.auth.get_db_session") as mock_get_db, patch(
        "src.app.api.routes.auth.log_audit_background"
    ) as mock_log_audit:
        mock_get_db.return_value.__aenter__.return_value = mock_session
        app.dependency_overrides[get_current_admin] = lambda: admin

        try:
            response = await async_client.post(
                f"/api/auth/admin/users/{target_user.id}/unlock"
            )
        finally:
            app.dependency_overrides.pop(get_current_admin, None)

    assert response.status_code == 200
    assert response.json()["user_id"] == target_user.id
    assert target_user.failed_login_attempts == 0
    assert target_user.locked_until is None
    assert mock_log_audit.call_args.kwargs["action"] == AuditAction.ACCOUNT_UNLOCK
    assert mock_log_audit.call_args.kwargs["user_id"] == admin.id
    assert mock_log_audit.call_args.kwargs["location_id"] == target_user.location_id
