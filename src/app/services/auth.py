from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from jose import jwt

from src.app.config import get_settings

logger = logging.getLogger(__name__)


class AuthService:
    """
    Service for handling authentication logic.
    """

    @staticmethod
    def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
        """Create a JWT access token."""
        token, _, _ = AuthService.build_access_token(data, expires_delta=expires_delta)
        return token

    @staticmethod
    def build_access_token(
        data: dict[str, Any], expires_delta: timedelta | None = None
    ) -> tuple[str, str, int]:
        """Create a JWT access token and return its JTI and TTL."""
        settings = get_settings()
        to_encode = data.copy()
        issued_at = datetime.now(timezone.utc)
        if expires_delta:
            expire = issued_at + expires_delta
        else:
            expire = issued_at + timedelta(minutes=settings.access_token_ttl_minutes)

        jti = str(uuid4())
        to_encode.update(
            {
                "exp": expire,
                "iat": issued_at,
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
                "jti": jti,
            }
        )

        encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        ttl_seconds = max(1, int((expire - issued_at).total_seconds()))
        return encoded_jwt, jti, ttl_seconds

    @staticmethod
    def decode_access_token(token: str) -> dict[str, Any]:
        """Decode and validate a JWT access token."""
        settings = get_settings()
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )

    @staticmethod
    def get_unverified_claims(token: str) -> dict[str, Any]:
        """Return token claims without signature verification."""
        return jwt.get_unverified_claims(token)

    @staticmethod
    def remaining_ttl_seconds(claims: dict[str, Any]) -> int:
        """Calculate the remaining access token lifetime from claims."""
        exp = claims.get("exp")
        if isinstance(exp, datetime):
            expires_at = exp.astimezone(timezone.utc)
        elif exp is not None:
            expires_at = datetime.fromtimestamp(float(exp), tz=timezone.utc)
        else:
            settings = get_settings()
            return settings.access_token_ttl_minutes * 60

        return max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
