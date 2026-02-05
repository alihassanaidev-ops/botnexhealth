"""
Authentication service for handling logins and token generation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select

from src.app.config import get_settings
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.user import User, UserRole
from src.app.services.audit import log_audit_background

logger = logging.getLogger(__name__)

# Password hashing configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# JWT Configuration
# Algorithm is loaded from settings


class AuthService:
    """
    Service for handling authentication logic.
    """
    
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against a hash."""
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    def get_password_hash(password: str) -> str:
        """Hash a password."""
        return pwd_context.hash(password)

    @staticmethod
    def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
        """Create a JWT access token."""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=15)
        
        to_encode.update({"exp": expire})
        
        encoded_jwt = jwt.encode(to_encode, get_settings().jwt_secret, algorithm=get_settings().jwt_algorithm)
        return encoded_jwt

    async def authenticate_user(self, email: str, password: str, tenant_id: str | None = None) -> User | None:
        """
        Authenticate a user by email and password.
        
        Logs the attempt for audit purposes.
        """
        async with get_db_session() as session:
            result = await session.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()
            
            # Prepare audit metadata
            audit_meta = {"email": email}
            
            if not user:
                # Log failed attempt (User not found)
                log_audit_background(
                    actor=AuditActor.API_CLIENT,
                    action=AuditAction.LOGIN,
                    target_resource="auth:login",
                    outcome=AuditOutcome.FAILURE_NOT_FOUND,
                    metadata=audit_meta,
                    tenant_id=tenant_id
                )
                return None
                
            if not self.verify_password(password, user.hashed_password):
                 # Log failed attempt (Bad password)
                log_audit_background(
                    actor=AuditActor.API_CLIENT,
                    action=AuditAction.LOGIN,
                    target_resource="auth:login",
                    outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
                    metadata=audit_meta,
                    tenant_id=tenant_id
                )
                return None
            
            if not user.is_active:
                return None

            # Log success
            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource=f"user:{user.id}",
                outcome=AuditOutcome.SUCCESS,
                metadata=audit_meta,
                tenant_id=tenant_id
            )
            
            # Update last login
            user.last_login_at = datetime.now(timezone.utc)
            session.add(user)
            # Commit happens on exit context
            
            return user
