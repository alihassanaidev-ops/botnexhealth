"""Targeted unit tests for JWT iss/aud/jti validation rejection paths."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import JWTError, jwt

from src.app.api.deps import get_current_user
from src.app.config import get_settings
from src.app.services.auth import AuthService


def _encode(claims: dict, *, secret: str | None = None, algorithm: str | None = None) -> str:
    settings = get_settings()
    return jwt.encode(
        claims,
        secret or settings.jwt_secret,
        algorithm=algorithm or settings.jwt_algorithm,
    )


def _valid_claims(**overrides) -> dict:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    base = {
        "sub": "user-1",
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": "jti-valid",
    }
    base.update(overrides)
    return base


def test_decode_accepts_valid_token() -> None:
    token = _encode(_valid_claims())
    decoded = AuthService.decode_access_token(token)
    assert decoded["sub"] == "user-1"
    assert decoded["jti"] == "jti-valid"


def test_decode_rejects_wrong_issuer() -> None:
    token = _encode(_valid_claims(iss="someone-else"))
    with pytest.raises(JWTError):
        AuthService.decode_access_token(token)


def test_decode_rejects_missing_issuer() -> None:
    claims = _valid_claims()
    claims.pop("iss")
    token = _encode(claims)
    with pytest.raises(JWTError):
        AuthService.decode_access_token(token)


def test_decode_rejects_wrong_audience() -> None:
    token = _encode(_valid_claims(aud="some-other-audience"))
    with pytest.raises(JWTError):
        AuthService.decode_access_token(token)


def test_decode_rejects_missing_audience() -> None:
    claims = _valid_claims()
    claims.pop("aud")
    token = _encode(claims)
    with pytest.raises(JWTError):
        AuthService.decode_access_token(token)


def test_decode_rejects_wrong_signing_secret() -> None:
    token = _encode(_valid_claims(), secret="wrong-secret-that-does-not-match")
    with pytest.raises(JWTError):
        AuthService.decode_access_token(token)


def test_decode_rejects_expired_token() -> None:
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    token = _encode(_valid_claims(iat=past - timedelta(minutes=20), exp=past))
    with pytest.raises(JWTError):
        AuthService.decode_access_token(token)


def test_build_access_token_includes_iss_aud_jti() -> None:
    settings = get_settings()
    token, jti, _ttl = AuthService.build_access_token(
        {"sub": "user-1", "role": "SUPER_ADMIN"}
    )
    decoded = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )
    assert decoded["iss"] == settings.jwt_issuer
    assert decoded["aud"] == settings.jwt_audience
    assert decoded["jti"] == jti
    assert "iat" in decoded
    assert "exp" in decoded


@pytest.mark.asyncio
async def test_get_current_user_rejects_token_without_jti() -> None:
    """Tokens missing jti must be rejected even if signature/iss/aud are valid."""
    claims = _valid_claims()
    claims.pop("jti")
    token = _encode(claims)

    with pytest.raises(Exception) as exc_info:
        await get_current_user(token=token)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_rejects_revoked_jti() -> None:
    """A token whose jti is on the deny-list must be rejected before DB lookup."""
    token = _encode(_valid_claims(sub="user-1", jti="revoked-jti"))

    with patch(
        "src.app.api.deps.RefreshTokenService.is_access_token_jti_revoked",
        new=AsyncMock(return_value=True),
    ) as is_revoked:
        with pytest.raises(Exception) as exc_info:
            await get_current_user(token=token)

    assert exc_info.value.status_code == 401
    is_revoked.assert_awaited_once_with("revoked-jti")


@pytest.mark.asyncio
async def test_get_current_user_returns_503_when_deny_list_unreachable() -> None:
    """If the deny-list backend is down we MUST fail closed, not let traffic through."""
    token = _encode(_valid_claims(sub="user-1", jti="some-jti"))

    with patch(
        "src.app.api.deps.RefreshTokenService.is_access_token_jti_revoked",
        new=AsyncMock(side_effect=ConnectionError("redis down")),
    ):
        with pytest.raises(Exception) as exc_info:
            await get_current_user(token=token)

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_get_current_user_passes_when_jti_not_revoked() -> None:
    """Happy path: valid token + non-revoked jti + active user → returns user."""
    token = _encode(_valid_claims(sub="user-1", jti="active-jti"))

    mock_user = MagicMock(id="user-1", is_active=True)
    mock_session = AsyncMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = mock_user
    mock_session.execute.return_value = query_result

    with patch(
        "src.app.api.deps.RefreshTokenService.is_access_token_jti_revoked",
        new=AsyncMock(return_value=False),
    ), patch("src.app.api.deps.get_db_session") as mock_get_db:
        mock_get_db.return_value.__aenter__.return_value = mock_session
        result = await get_current_user(token=token)

    assert result is mock_user
