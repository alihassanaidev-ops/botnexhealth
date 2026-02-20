"""
Authentication routes.

Two login flows:
1. POST /auth/token          — Admin login with local email+password (bcrypt)
2. POST /auth/supabase/token — Tenant login via Supabase token exchange
"""

import logging
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select

from src.app.api.deps import get_current_active_user
from src.app.database import get_db_session
from src.app.models.user import User
from src.app.services.auth import AuthService
from src.app.services.supabase_service import SupabaseService
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.services.audit import log_audit_background
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

# Rate limiter — keyed by client IP
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class Token(BaseModel):
    access_token: str
    token_type: str


class SupabaseTokenRequest(BaseModel):
    access_token: str


class UserRead(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool
    tenant_id: str | None = None


@router.post("/supabase/token", response_model=Token)
@limiter.limit("5/minute")
async def exchange_supabase_token(request: Request, data: SupabaseTokenRequest) -> Token:
    """
    Exchange a Supabase access token for a local JWT.
    
    Unified Login Flow (Admins & Tenants):
    1. Frontend authenticates with Supabase (email + password / magic link)
    2. Frontend sends the Supabase access_token here
    3. We verify it server-side via Supabase admin API
    4. We look up the matching local user (ADMIN or TENANT) and issue our own JWT
    """
    supabase_service = SupabaseService()
    
    # Audit: Attempting login
    # We don't have the user ID yet, so we log with a placeholder or just wait for success/failure
    # Ideally, we log the result.
    
    audit_meta = {"action": "token_exchange"}

    # Verify the Supabase token and get the user
    try:
        supabase_user = supabase_service.get_user_by_token(data.access_token)
    except Exception as e:
        logger.warning(f"Supabase token verification failed: {e}")
        # Audit failure would be hard here without an actor, but strictly we could log system events.
        # For now, we rely on the Exception to be caught if we wrap this in a service, 
        # but here we just return 401.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Supabase token",
        )

    if not supabase_user or not supabase_user.email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not resolve user from Supabase token",
        )

    audit_meta["supabase_uid"] = str(supabase_user.id)

    # Find the matching local user by Supabase UUID (user.id = auth.users.id)
    supabase_uid = str(supabase_user.id)
    async with get_db_session() as session:
        result = await session.execute(
            select(User).where(User.id == supabase_uid)
        )
        user = result.scalar_one_or_none()

        if not user:
            # Audit: Failure - User not found locally
            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource="auth:login",
                outcome=AuditOutcome.FAILURE_NOT_FOUND,
                metadata=audit_meta
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No local account found for this Supabase user",
            )

        # Audit: Context with actual user ID
        audit_meta["user_id"] = user.id
        audit_meta["role"] = user.role

        target_resource = f"user:{user.id}"

        if not user.is_active:
            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource=target_resource,
                outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
                metadata={**audit_meta, "reason": "account_inactive"},
                tenant_id=user.tenant_id
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive",
            )

    # Issue local JWT (sub = user UUID)
    auth_service = AuthService()
    access_token = auth_service.create_access_token(
        data={
            "sub": user.id,
            "role": user.role,
            "tenant_id": user.tenant_id,
        },
        expires_delta=timedelta(minutes=15)
    )

    # Audit: Success
    log_audit_background(
        actor=user.role, # The user is now the actor
        action=AuditAction.LOGIN,
        target_resource=target_resource,
        outcome=AuditOutcome.SUCCESS,
        metadata=audit_meta,
        tenant_id=user.tenant_id
    )

    return Token(access_token=access_token, token_type="bearer")


@router.get("/users/me", response_model=UserRead)
@limiter.limit("30/minute")
async def read_users_me(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """
    Get current user profile.
    """
    return current_user
