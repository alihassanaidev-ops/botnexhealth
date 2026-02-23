"""
Authentication routes.

Two login flows:
1. POST /auth/token          — Admin login with local email+password (bcrypt)
2. POST /auth/supabase/token — Tenant login via Supabase token exchange
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select

from src.app.api.deps import get_current_active_user, get_current_admin
from src.app.config import settings
from src.app.database import get_db_session
from src.app.models.user import User
from src.app.services.auth import AuthService
from src.app.services.supabase_service import SupabaseService
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.services.audit import log_audit_background
from src.app.api.rate_limit import limiter, RATE_AUTH

logger = logging.getLogger(__name__)



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
@limiter.limit(RATE_AUTH)
async def exchange_supabase_token(request: Request, data: SupabaseTokenRequest) -> Token:
    """
    Exchange a Supabase access token for a local JWT.

    Unified Login Flow (Admins & Tenants):
    1. Frontend authenticates with Supabase (email + password / magic link)
    2. Frontend sends the Supabase access_token here
    3. We verify it server-side via Supabase admin API
    4. We look up the matching local user, check lockout, then issue our own JWT

    HIPAA §164.312(d): Accounts lock after MAX_FAILED_LOGIN_ATTEMPTS consecutive
    failures. Lockout duration is ACCOUNT_LOCKOUT_MINUTES. Admins can unlock via
    POST /admin/users/{user_id}/unlock.
    """
    supabase_service = SupabaseService()

    # Capture client IP once — used in every audit log for this request
    forwarded_for = request.headers.get("x-forwarded-for")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
        request.client.host if request.client else None
    )

    audit_meta: dict = {"action": "token_exchange"}
    if client_ip:
        audit_meta["ip_address"] = client_ip

    # --- Step 1: Verify Supabase token ---
    try:
        supabase_user = supabase_service.get_user_by_token(data.access_token)
    except Exception as e:
        logger.warning(f"Supabase token verification failed: {e}")
        log_audit_background(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.LOGIN,
            target_resource="auth:login",
            outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
            metadata={**audit_meta, "reason": "invalid_supabase_token"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    if not supabase_user or not supabase_user.email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    audit_meta["supabase_uid"] = str(supabase_user.id)
    supabase_uid = str(supabase_user.id)

    # --- Steps 2–5: DB checks and counter updates in a single session ---
    async with get_db_session() as session:
        result = await session.execute(select(User).where(User.id == supabase_uid))
        user = result.scalar_one_or_none()

        # Step 2: Local user must exist
        if not user:
            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource="auth:login",
                outcome=AuditOutcome.FAILURE_NOT_FOUND,
                metadata=audit_meta,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )

        audit_meta["user_id"] = user.id
        audit_meta["role"] = user.role
        target_resource = f"user:{user.id}"

        # Step 3: Check account lockout
        if user.is_locked():
            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource=target_resource,
                outcome=AuditOutcome.FAILURE_ACCOUNT_LOCKED,
                metadata={
                    **audit_meta,
                    "reason": "account_locked",
                    "locked_until": user.locked_until.isoformat(),
                },
                tenant_id=user.tenant_id,
            )
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account is temporarily locked. Contact your administrator.",
            )

        # Step 4: Check active status — count as a failed attempt
        if not user.is_active:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.max_failed_login_attempts:
                user.locked_until = datetime.now(timezone.utc) + timedelta(
                    minutes=settings.account_lockout_minutes
                )
                logger.warning(
                    f"Account locked after {user.failed_login_attempts} failed attempts: "
                    f"user={user.id}"
                )
            # session auto-commits on exit
            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource=target_resource,
                outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
                metadata={**audit_meta, "reason": "account_inactive"},
                tenant_id=user.tenant_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive",
            )

        # Step 5: Success — reset lockout state
        user.failed_login_attempts = 0
        user.locked_until = None
        # session auto-commits on exit

    # --- Issue local JWT (sub = user UUID) ---
    auth_service = AuthService()
    access_token = auth_service.create_access_token(
        data={
            "sub": user.id,
            "role": user.role,
            "tenant_id": user.tenant_id,
        },
        expires_delta=timedelta(minutes=15),
    )

    log_audit_background(
        actor=user.role,
        action=AuditAction.LOGIN,
        target_resource=target_resource,
        outcome=AuditOutcome.SUCCESS,
        metadata=audit_meta,
        tenant_id=user.tenant_id,
    )

    return Token(access_token=access_token, token_type="bearer")


# =============================================================================
# Admin: Account Unlock
# =============================================================================

class UnlockResponse(BaseModel):
    message: str
    user_id: str


@router.post("/admin/users/{user_id}/unlock", response_model=UnlockResponse)
@limiter.limit("20/minute")
async def unlock_user_account(
    request: Request,
    user_id: str,
    admin: Annotated[User, Depends(get_current_admin)],
) -> UnlockResponse:
    """
    Unlock a locked user account and reset the failed login counter.

    HIPAA §164.312(d): Only admins can unlock accounts. The action is
    audit-logged with the acting admin's identity.
    """
    async with get_db_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        target_user = result.scalar_one_or_none()

        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        was_locked = target_user.is_locked()
        target_user.failed_login_attempts = 0
        target_user.locked_until = None
        # session auto-commits on exit

    log_audit_background(
        actor=AuditActor.ADMIN,
        action=AuditAction.ACCOUNT_UNLOCK,
        target_resource=f"user:{user_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "admin_id": admin.id,
            "target_user_id": user_id,
            "was_locked": was_locked,
        },
        tenant_id=target_user.tenant_id,
    )

    return UnlockResponse(
        message="Account unlocked successfully",
        user_id=user_id,
    )


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
