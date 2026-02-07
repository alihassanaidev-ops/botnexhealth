"""
Authentication service for handling logins and token generation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import jwt
from sqlalchemy import select

from src.app.config import get_settings
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.user import User, UserRole
from src.app.services.audit import log_audit_background

logger = logging.getLogger(__name__)

# HIPAA: Lock account after this many consecutive failed login attempts
MAX_FAILED_LOGIN_ATTEMPTS = 5


class AuthService:
    """
    Service for handling authentication logic.
    """

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against a hash."""
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )

    @staticmethod
    def get_password_hash(password: str) -> str:
        """Hash a password."""
        return bcrypt.hashpw(
            password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')

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

    async def authenticate_user(self, email: str, password: str, tenant_id: str | None = None) -> User | None:
        """
        Authenticate a user by email and password.

        - Returns None on any failure (user not found, bad password, inactive, locked).
        - Tracks failed_login_attempts and locks account after MAX_FAILED_LOGIN_ATTEMPTS.
        - Users created via Supabase invite (hashed_password=None) cannot log in here;
          they authenticate through Supabase Auth and use the JWT exchange flow.
        """
        async with get_db_session() as session:
            result = await session.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()

            audit_meta = {"email": email}

            if not user:
                log_audit_background(
                    actor=AuditActor.API_CLIENT,
                    action=AuditAction.LOGIN,
                    target_resource="auth:login",
                    outcome=AuditOutcome.FAILURE_NOT_FOUND,
                    metadata=audit_meta,
                    tenant_id=tenant_id
                )
                return None

            if not user.is_active:
                log_audit_background(
                    actor=AuditActor.API_CLIENT,
                    action=AuditAction.LOGIN,
                    target_resource=f"user:{user.id}",
                    outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
                    metadata={**audit_meta, "reason": "account_inactive"},
                    tenant_id=tenant_id
                )
                return None

            # HIPAA: Check if account is locked due to too many failed attempts
            if user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                log_audit_background(
                    actor=AuditActor.API_CLIENT,
                    action=AuditAction.LOGIN,
                    target_resource=f"user:{user.id}",
                    outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
                    metadata={**audit_meta, "reason": "account_locked"},
                    tenant_id=tenant_id
                )
                return None

            # Users created via Supabase invite have no local password.
            # They must authenticate through Supabase Auth, not this endpoint.
            if not user.hashed_password:
                log_audit_background(
                    actor=AuditActor.API_CLIENT,
                    action=AuditAction.LOGIN,
                    target_resource=f"user:{user.id}",
                    outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
                    metadata={**audit_meta, "reason": "no_local_password"},
                    tenant_id=tenant_id
                )
                return None

            if not self.verify_password(password, user.hashed_password):
                # Increment failed login counter
                user.failed_login_attempts += 1
                session.add(user)
                log_audit_background(
                    actor=AuditActor.API_CLIENT,
                    action=AuditAction.LOGIN,
                    target_resource=f"user:{user.id}",
                    outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
                    metadata={**audit_meta, "failed_attempts": user.failed_login_attempts},
                    tenant_id=tenant_id
                )
                return None

            # Success — reset failed attempts counter
            user.failed_login_attempts = 0
            user.last_login_at = datetime.now(timezone.utc)
            session.add(user)

            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource=f"user:{user.id}",
                outcome=AuditOutcome.SUCCESS,
                metadata=audit_meta,
                tenant_id=tenant_id
            )

            return user
