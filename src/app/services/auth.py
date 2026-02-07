from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

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
        settings = get_settings()
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=15)

        to_encode.update({"exp": expire})

        encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        return encoded_jwt
