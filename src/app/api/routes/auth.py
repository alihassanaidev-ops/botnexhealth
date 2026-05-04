"""Authentication routes."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import select

from src.app.api.deps import get_current_active_user, get_current_admin
from src.app.config import settings
from src.app.database import get_db_session
from src.app.models.user import User, InviteStatus
from src.app.services.auth import AuthService
from src.app.services.auth_email_service import AuthEmailService
from src.app.services.password_service import PasswordService
from src.app.services.refresh_token_service import (
    RefreshTokenReplayError,
    RefreshTokenService,
)
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.security import get_client_ip
from src.app.services.audit import log_audit, log_audit_background
from src.app.api.rate_limit import RATE_AUTH, limiter

logger = logging.getLogger(__name__)


# Pre-computed Argon2id hash used to keep /login response timing constant when
# the user does not exist — without this, an attacker can enumerate accounts
# by measuring how long the request takes (real Argon2 verify ~200 ms vs an
# instant 401 for a missing user). Stable for the process lifetime.
_LOGIN_TIMING_DUMMY_HASH = PasswordService.compute_timing_safe_dummy_hash()


router = APIRouter(prefix="/auth", tags=["Authentication"])


class AuthSession(BaseModel):
    """Login/refresh response. Refresh token is delivered via HttpOnly cookie."""
    access_token: str
    token_type: str


class MessageResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: str
    redirect_url: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class SetPasswordRequest(BaseModel):
    token: str
    password: str


class UserRead(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool
    invite_status: str = "ACCEPTED"
    institution_id: str | None = None
    location_id: str | None = None


def _client_ip(request: Request) -> str | None:
    return get_client_ip(
        forwarded_for=request.headers.get("x-forwarded-for"),
        direct_host=request.client.host if request.client else None,
    )


def _allowed_origin_set() -> frozenset[str]:
    """Origins permitted for state-changing cookie-authenticated requests."""
    raw = (settings.cors_allowed_origins or "").strip()
    if not raw or raw == "*":
        return frozenset()
    out: set[str] = set()
    for entry in raw.split(","):
        candidate = entry.strip().rstrip("/")
        if candidate and candidate != "*":
            out.add(candidate.lower())
    return frozenset(out)


def _origin_root(value: str | None) -> str | None:
    """Reduce an Origin/Referer header to just scheme://host[:port]."""
    if not value:
        return None
    from urllib.parse import urlsplit

    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _enforce_same_origin(request: Request) -> None:
    """Reject cross-origin POSTs to cookie-authenticated endpoints.

    SameSite=Strict on the refresh cookie is the primary defense; this is a
    second layer that costs nothing and stops malformed requests where the
    Origin/Referer cannot be confirmed against the deployed CORS allowlist.
    """
    allowed = _allowed_origin_set()
    if not allowed:
        # Wildcard / no explicit origins configured (typically local dev).
        return

    origin = _origin_root(request.headers.get("origin"))
    if origin is None:
        origin = _origin_root(request.headers.get("referer"))

    if origin is None or origin not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-origin request rejected",
        )


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _audit_user_id(user: User) -> str:
    return str(user.id)


def _audit_location_id(user: User) -> str | None:
    return str(user.location_id) if user.location_id else None


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite.lower(),
        path=settings.refresh_cookie_path,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite.lower(),
    )


def _get_refresh_cookie(request: Request) -> str | None:
    return request.cookies.get(settings.refresh_cookie_name)


async def _issue_access_token(user: User) -> AuthSession:
    auth_service = AuthService()
    access_token, jti, ttl_seconds = auth_service.build_access_token(
        data={
            "sub": user.id,
            "role": user.role,
            "institution_id": user.institution_id,
            "location_id": user.location_id,
        },
        expires_delta=timedelta(minutes=settings.access_token_ttl_minutes),
    )
    await RefreshTokenService.register_access_token(user.id, jti, ttl_seconds=ttl_seconds)
    return AuthSession(access_token=access_token, token_type="bearer")


async def _issue_auth_session(
    user: User,
    response: Response,
    *,
    revoke_existing: bool = False,
) -> AuthSession:
    try:
        if revoke_existing:
            await RefreshTokenService.revoke_all_for_user(user.id)
            await RefreshTokenService.revoke_all_access_tokens_for_user(user.id)
        refresh_token = await RefreshTokenService.issue_token(user.id)
        session = await _issue_access_token(user)
    except Exception as e:
        logger.error("Failed to issue auth session: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication session store is unavailable",
        )

    _set_refresh_cookie(response, refresh_token)
    return session


async def _revoke_access_token_from_request(request: Request) -> None:
    bearer_token = _extract_bearer_token(request)
    if not bearer_token:
        return

    try:
        claims = AuthService.decode_access_token(bearer_token)
    except JWTError:
        return

    jti = claims.get("jti")
    if not jti:
        return

    await RefreshTokenService.revoke_access_token_jti(
        jti,
        user_id=claims.get("sub"),
        ttl_seconds=AuthService.remaining_ttl_seconds(claims),
    )


def _register_failed_login_attempt(user: User) -> bool:
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= settings.max_failed_login_attempts:
        user.locked_until = datetime.now(timezone.utc) + timedelta(
            minutes=settings.account_lockout_minutes
        )
        return True
    return False


def _clear_password_reset_state(user: User) -> None:
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None


def _clear_invite_state(user: User) -> None:
    user.invite_token_hash = None
    user.invite_expires_at = None


def _set_password_on_user(user: User, password: str) -> None:
    user.password_hash = PasswordService.hash_password(password)
    user.password_set_at = datetime.now(timezone.utc)
    user.failed_login_attempts = 0
    user.locked_until = None


@router.post("/login", response_model=AuthSession)
@limiter.limit(RATE_AUTH)
async def login(request: Request, response: Response, data: LoginRequest) -> AuthSession:
    """Authenticate a user with local email/password credentials."""
    email = _normalize_email(data.email)
    client_ip = _client_ip(request)
    audit_meta: dict[str, str] = {"action": "local_login"}
    if client_ip:
        audit_meta["ip_address"] = client_ip

    async with get_db_session() as session:
        result = await session.execute(
            select(User).where(
                User.email == email,
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            # Constant-time defense against email enumeration: do a dummy
            # Argon2 verify so the not-found branch takes ~the same time as
            # the wrong-password branch.
            PasswordService.verify_password(data.password, _LOGIN_TIMING_DUMMY_HASH)
            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource="auth:login",
                outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
                metadata={**audit_meta, "reason": "invalid_credentials"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        target_resource = f"user:{user.id}"
        audit_meta["user_id"] = user.id
        audit_meta["role"] = user.role

        if user.is_locked():
            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource=target_resource,
                outcome=AuditOutcome.FAILURE_ACCOUNT_LOCKED,
                metadata={**audit_meta, "reason": "account_locked"},
                institution_id=user.institution_id,
                user_id=_audit_user_id(user),
                location_id=_audit_location_id(user),
            )
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account is temporarily locked. Contact your administrator.",
            )

        if not user.is_active:
            _register_failed_login_attempt(user)
            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource=target_resource,
                outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
                metadata={**audit_meta, "reason": "account_inactive"},
                institution_id=user.institution_id,
                user_id=_audit_user_id(user),
                location_id=_audit_location_id(user),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive",
            )

        if not PasswordService.verify_password(data.password, user.password_hash):
            is_now_locked = _register_failed_login_attempt(user)
            outcome = (
                AuditOutcome.FAILURE_ACCOUNT_LOCKED
                if is_now_locked
                else AuditOutcome.FAILURE_UNAUTHORIZED
            )
            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.LOGIN,
                target_resource=target_resource,
                outcome=outcome,
                metadata={**audit_meta, "reason": "invalid_credentials"},
                institution_id=user.institution_id,
                user_id=_audit_user_id(user),
                location_id=_audit_location_id(user),
            )
            if is_now_locked:
                raise HTTPException(
                    status_code=status.HTTP_423_LOCKED,
                    detail="Account is temporarily locked. Contact your administrator.",
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        audit_request_id = str(uuid4())
        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.LOGIN,
            target_resource=target_resource,
            outcome=AuditOutcome.INITIATED,
            metadata={**audit_meta, "phase": "intent"},
            institution_id=user.institution_id,
            user_id=_audit_user_id(user),
            location_id=_audit_location_id(user),
            request_id=audit_request_id,
        )

        user.failed_login_attempts = 0
        user.locked_until = None
        if PasswordService.needs_rehash(user.password_hash):
            user.password_hash = PasswordService.hash_password(data.password)
            user.password_set_at = datetime.now(timezone.utc)
    session = await _issue_auth_session(user, response)

    # Durable: a successful login is a security-relevant event. The INITIATED
    # row above is written before login state mutation/session issuance; this
    # completion row closes the loop for reporting and reconciliation.
    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.LOGIN,
        target_resource=f"user:{user.id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={**audit_meta, "phase": "complete"},
        institution_id=user.institution_id,
        user_id=_audit_user_id(user),
        location_id=_audit_location_id(user),
        request_id=audit_request_id,
    )
    return session


@router.post("/token", response_model=AuthSession)
@limiter.limit(RATE_AUTH)
async def login_oauth_form(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> AuthSession:
    """OAuth2 form-compatible alias for local login."""
    return await login(
        request=request,
        response=response,
        data=LoginRequest(email=form_data.username, password=form_data.password),
    )


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("10/minute")
async def forgot_password(request: Request, data: ForgotPasswordRequest) -> MessageResponse:
    """Generate a password reset token and send a reset email if the account exists."""
    email = _normalize_email(data.email)
    client_ip = _client_ip(request)
    email_service = AuthEmailService()

    if data.redirect_url:
        try:
            email_service.resolve_redirect_url(
                redirect_url=data.redirect_url,
                default_path="/set-password",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    async with get_db_session() as session:
        result = await session.execute(
            select(User).where(
                User.email == email,
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()

        if user and user.is_active:
            token = PasswordService.generate_one_time_token()
            user.password_reset_token_hash = PasswordService.hash_token(token)
            user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(
                minutes=settings.password_reset_token_ttl_minutes
            )
            await email_service.send_password_reset_email(
                email=user.email,
                token=token,
                redirect_url=data.redirect_url,
            )

            log_audit_background(
                actor=AuditActor.API_CLIENT,
                action=AuditAction.PASSWORD_RESET_REQUEST,
                target_resource=f"user:{user.id}",
                outcome=AuditOutcome.SUCCESS,
                metadata={
                    "action": "forgot_password",
                    "ip_address": client_ip,
                },
                institution_id=user.institution_id,
                user_id=_audit_user_id(user),
                location_id=_audit_location_id(user),
            )

    return MessageResponse(
        message="If an account exists for that email, a password reset email has been sent."
    )


@router.post("/reset-password", response_model=AuthSession)
@limiter.limit("20/minute")
async def reset_password(
    request: Request,
    response: Response,
    data: ResetPasswordRequest,
) -> AuthSession:
    """Consume a password reset token, set a new password, and issue a JWT."""
    token_hash = PasswordService.hash_token(data.token)
    client_ip = _client_ip(request)

    async with get_db_session() as session:
        result = await session.execute(
            select(User).where(
                User.password_reset_token_hash == token_hash,
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()

        if (
            not user
            or not user.password_reset_expires_at
            or user.password_reset_expires_at < datetime.now(timezone.utc)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired password reset token",
            )

        _set_password_on_user(user, data.password)
        _clear_password_reset_state(user)
    session = await _issue_auth_session(user, response, revoke_existing=True)

    # Durable: password reset is a credential change; the audit row must be
    # persistent for HIPAA §164.312(d) reviews.
    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.PASSWORD_RESET_COMPLETE,
        target_resource=f"user:{user.id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"action": "reset_password", "ip_address": client_ip},
        institution_id=user.institution_id,
        user_id=_audit_user_id(user),
        location_id=_audit_location_id(user),
    )
    return session


@router.post("/set-password", response_model=AuthSession)
@limiter.limit("20/minute")
async def set_password(
    request: Request,
    response: Response,
    data: SetPasswordRequest,
) -> AuthSession:
    """Consume an invite token, set a password, and issue a JWT."""
    token_hash = PasswordService.hash_token(data.token)
    client_ip = _client_ip(request)

    async with get_db_session() as session:
        result = await session.execute(
            select(User).where(
                User.invite_token_hash == token_hash,
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()

        if (
            not user
            or not user.invite_expires_at
            or user.invite_expires_at < datetime.now(timezone.utc)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired invite token",
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive",
            )

        _set_password_on_user(user, data.password)
        user.invite_status = InviteStatus.ACCEPTED.value
        _clear_invite_state(user)
        _clear_password_reset_state(user)
    session = await _issue_auth_session(user, response, revoke_existing=True)

    # Durable: initial password-from-invite is a credential change.
    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.PASSWORD_SET,
        target_resource=f"user:{user.id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"action": "set_password", "ip_address": client_ip},
        institution_id=user.institution_id,
        user_id=_audit_user_id(user),
        location_id=_audit_location_id(user),
    )
    return session


@router.post("/refresh", response_model=AuthSession)
@limiter.limit("60/minute")
async def refresh_session(request: Request, response: Response) -> AuthSession:
    """Rotate the refresh-token cookie and issue a new access token."""
    _enforce_same_origin(request)
    client_ip = _client_ip(request)
    refresh_token = _get_refresh_cookie(request)

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    try:
        user_id = await RefreshTokenService.get_user_id_for_token(refresh_token)
    except Exception as e:
        logger.error("Failed to validate refresh token: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication session store is unavailable",
        )

    if not user_id:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    async with get_db_session() as session:
        result = await session.execute(
            select(User).where(
                User.id == user_id,
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            await RefreshTokenService.revoke_token(refresh_token)
            _clear_refresh_cookie(response)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )

        if user.is_locked():
            await RefreshTokenService.revoke_token(refresh_token)
            _clear_refresh_cookie(response)
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account is temporarily locked. Contact your administrator.",
            )

        if not user.is_active:
            await RefreshTokenService.revoke_token(refresh_token)
            _clear_refresh_cookie(response)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive",
            )

    try:
        new_refresh_token = await RefreshTokenService.rotate_token(user.id, refresh_token)
    except RefreshTokenReplayError:
        # Stolen-token signal — every session for this user is already revoked
        # by the service. Audit aggressively (durable: stolen-token detection
        # is a security event we never want to lose), clear the cookie, 401.
        await log_audit(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.LOGIN,
            target_resource=f"user:{user.id}",
            outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
            metadata={
                "action": "refresh_token_replay_detected",
                "ip_address": client_ip,
            },
            institution_id=user.institution_id,
            user_id=_audit_user_id(user),
            location_id=_audit_location_id(user),
        )
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token replay detected. Please sign in again.",
        )
    except Exception as e:
        logger.error("Failed to rotate refresh token: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication session store is unavailable",
        )

    if not new_refresh_token:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    try:
        access = await _issue_access_token(user)
    except Exception as e:
        logger.error("Failed to issue access token: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication session store is unavailable",
        ) from e
    _set_refresh_cookie(response, new_refresh_token)
    log_audit_background(
        actor=AuditActor.ADMIN,
        action=AuditAction.LOGIN,
        target_resource=f"user:{user.id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"action": "refresh_session", "ip_address": client_ip},
        institution_id=user.institution_id,
        user_id=_audit_user_id(user),
        location_id=_audit_location_id(user),
    )
    return access


@router.post("/logout", response_model=MessageResponse)
@limiter.limit("60/minute")
async def logout(request: Request, response: Response) -> MessageResponse:
    """Invalidate the refresh-token cookie and access token. Idempotent."""
    _enforce_same_origin(request)
    client_ip = _client_ip(request)
    revoked_user_id: str | None = None
    refresh_token = _get_refresh_cookie(request)

    try:
        if refresh_token:
            revoked_user_id = await RefreshTokenService.revoke_token(refresh_token)
        await _revoke_access_token_from_request(request)
    except Exception as e:
        logger.error("Failed to revoke refresh token: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication session store is unavailable",
        )

    _clear_refresh_cookie(response)

    if revoked_user_id:
        log_audit_background(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.LOGIN,
            target_resource=f"user:{revoked_user_id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={"action": "logout", "ip_address": client_ip},
            user_id=revoked_user_id,
        )

    return MessageResponse(message="Logged out successfully")


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
        result = await session.execute(
            select(User).where(
                User.id == user_id,
                User.deleted_at.is_(None),
            )
        )
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

    # Durable: admin-initiated account unlock is a security-relevant action.
    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.ACCOUNT_UNLOCK,
        target_resource=f"user:{user_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "admin_id": admin.id,
            "target_user_id": user_id,
            "was_locked": was_locked,
        },
        institution_id=target_user.institution_id,
        user_id=_audit_user_id(admin),
        location_id=_audit_location_id(target_user),
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
