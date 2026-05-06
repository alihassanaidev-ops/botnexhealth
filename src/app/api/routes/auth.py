"""Authentication routes."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import select

from src.app.api.deps import get_current_active_user, get_current_admin
from src.app.config import settings
from src.app.database import RlsContext, get_db_session, use_rls_context
from src.app.models.user import User, InviteStatus, UserRole
from src.app.services.auth import AuthService
from src.app.services.auth_email_service import AuthEmailService
from src.app.services.password_service import PasswordService
from src.app.services.refresh_token_service import (
    RefreshTokenReplayError,
    RefreshSession,
    RefreshTokenService,
)
from src.app.services.mfa import (
    MfaError,
    MfaService,
    MfaStatus,
    MfaStoreUnavailable,
    MfaTicket,
    MfaTicketInvalid,
    MfaTicketService,
    MfaVerificationFailed,
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
    status: Literal["authenticated"] = "authenticated"
    access_token: str
    token_type: str
    recovery_codes: list[str] | None = None


class MfaChallengeResponse(BaseModel):
    """Password accepted; MFA must be completed before session issuance."""
    status: Literal["mfa_required", "mfa_setup_required"]
    mfa_ticket: str
    methods: list[str] = []
    setup_methods: list[str] = []
    expires_in_seconds: int = MfaTicketService.TTL_SECONDS
    role: str
    email: str


AuthResult = AuthSession | MfaChallengeResponse


class MessageResponse(BaseModel):
    message: str


class MfaTicketRequest(BaseModel):
    mfa_ticket: str


class WebAuthnOptionsResponse(BaseModel):
    options: dict[str, Any]


class WebAuthnRegistrationVerifyRequest(MfaTicketRequest):
    credential: dict[str, Any]
    device_label: str | None = None


class WebAuthnAuthenticationVerifyRequest(MfaTicketRequest):
    credential: dict[str, Any]


class TotpSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TotpVerifyRequest(MfaTicketRequest):
    code: str


class RecoveryCodeVerifyRequest(MfaTicketRequest):
    code: str


class MfaStatusResponse(BaseModel):
    webauthn_count: int
    totp_enabled: bool
    recovery_codes_remaining: int
    methods: list[str]


class WebAuthnCredentialSummary(BaseModel):
    """Public-facing passkey metadata. Never includes the public key."""
    id: str
    device_label: str | None
    aaguid: str | None
    credential_device_type: str | None
    credential_backed_up: bool
    transports: list[str] | None
    created_at: datetime
    last_used_at: datetime | None


class WebAuthnCredentialListResponse(BaseModel):
    credentials: list[WebAuthnCredentialSummary]


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]


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
        max_age=settings.refresh_token_ttl_minutes * 60,
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


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _mfa_exception_to_http(exc: MfaError) -> HTTPException:
    if isinstance(exc, MfaStoreUnavailable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MFA session store is unavailable",
        )
    if isinstance(exc, MfaTicketInvalid):
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc) or "Invalid or expired MFA ticket",
        )
    if isinstance(exc, MfaVerificationFailed):
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc) or "MFA verification failed",
        )
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _mfa_response(
    *,
    user: User,
    ticket: str,
    mfa_status: MfaStatus,
) -> MfaChallengeResponse:
    is_enrolled = mfa_status.enrolled_for_role(user.role)
    return MfaChallengeResponse(
        status="mfa_required" if is_enrolled else "mfa_setup_required",
        mfa_ticket=ticket,
        methods=mfa_status.available_methods_for_role(user.role) if is_enrolled else [],
        setup_methods=[] if is_enrolled else mfa_status.setup_methods_for_role(user.role),
        role=user.role,
        email=user.email,
    )


async def _create_mfa_ticket_response(
    *,
    request: Request,
    user: User,
    mfa_status: MfaStatus,
    audit_request_id: str,
    purpose: str,
    revoke_existing: bool = False,
    post_password_action: str | None = None,
) -> MfaChallengeResponse:
    try:
        ticket = await MfaTicketService.create(
            user=user,
            purpose=purpose,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
            audit_request_id=audit_request_id,
            revoke_existing=revoke_existing,
            post_password_action=post_password_action,
        )
    except MfaError as exc:
        raise _mfa_exception_to_http(exc) from exc
    return _mfa_response(user=user, ticket=ticket, mfa_status=mfa_status)


async def _ticket_from_request(
    request: Request,
    token: str,
    *,
    purpose: str | None = None,
) -> MfaTicket:
    try:
        return await MfaTicketService.get(
            token,
            client_ip=_client_ip(request),
            user_agent=_user_agent(request),
            purpose=purpose,
        )
    except MfaError as exc:
        raise _mfa_exception_to_http(exc) from exc


def _mfa_amr(method: str) -> tuple[str, ...]:
    return ("pwd", method)


async def _issue_access_token(user: User) -> AuthSession:
    return await _issue_mfa_bound_access_token(
        user,
        amr=("pwd", "mfa"),
        auth_time=int(datetime.now(timezone.utc).timestamp()),
    )


async def _issue_mfa_bound_access_token(
    user: User,
    *,
    amr: tuple[str, ...],
    auth_time: int,
) -> AuthSession:
    auth_service = AuthService()
    access_token, jti, ttl_seconds = auth_service.build_access_token(
        data={
            "sub": user.id,
            "role": user.role,
            "institution_id": user.institution_id,
            "location_id": user.location_id,
            "mfa": True,
            "amr": list(amr),
            "auth_time": auth_time,
            "mfa_time": int(datetime.now(timezone.utc).timestamp()),
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
    amr: tuple[str, ...] = ("pwd", "mfa"),
    auth_time: int | None = None,
) -> AuthSession:
    try:
        issued_auth_time = auth_time or int(datetime.now(timezone.utc).timestamp())
        if revoke_existing:
            await RefreshTokenService.revoke_all_for_user(user.id)
            await RefreshTokenService.revoke_all_access_tokens_for_user(user.id)
        refresh_token = await RefreshTokenService.issue_token(
            user.id,
            mfa=True,
            amr=amr,
            auth_time=issued_auth_time,
        )
        session = await _issue_mfa_bound_access_token(
            user,
            amr=amr,
            auth_time=issued_auth_time,
        )
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


async def _register_failed_login_attempt(session, user: User) -> bool:  # noqa: ANN001
    """Atomically increment failed_login_attempts; set locked_until on threshold.

    Uses an atomic UPDATE ... RETURNING so two concurrent failed logins
    cannot both read attempts=N and both write N+1 (lost update). Without
    this, an attacker could race past the lockout threshold by issuing
    requests faster than each transaction's read+write cycle.
    """
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                """
                UPDATE users
                SET
                    failed_login_attempts = failed_login_attempts + 1,
                    locked_until = CASE
                        WHEN failed_login_attempts + 1 >= :threshold
                        THEN NOW() + make_interval(mins => :lockout_min)
                        ELSE locked_until
                    END
                WHERE id = :uid
                RETURNING failed_login_attempts, locked_until
                """
            ),
            {
                "threshold": settings.max_failed_login_attempts,
                "lockout_min": settings.account_lockout_minutes,
                "uid": user.id,
            },
        )
    ).one()

    # Reflect canonical DB state back onto the in-memory User instance so
    # the caller sees consistent values for any downstream metadata.
    user.failed_login_attempts = row[0]
    user.locked_until = row[1]
    return row[0] >= settings.max_failed_login_attempts


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


def _set_password_or_400(user: User, password: str) -> None:
    try:
        _set_password_on_user(user, password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


async def _user_for_mfa_ticket(session, ticket: MfaTicket) -> User:  # noqa: ANN001
    result = await session.execute(
        select(User).where(
            User.id == ticket.user_id,
            User.deleted_at.is_(None),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA ticket",
        )
    if user.is_locked():
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account is temporarily locked. Contact your administrator.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )
    return user


async def _audit_mfa_failure(
    *,
    request: Request,
    user: User,
    ticket: MfaTicket,
    method: str,
    phase: str,
    error: MfaError,
    enrolled: bool = False,
) -> None:
    """Persist a HIPAA §164.312(b) audit row for a failed MFA verification.

    Failed MFA attempts are security-relevant events under
    §164.312(b) (Audit Controls) and §164.308(a)(1)(ii)(D) (Information
    System Activity Review). The previous code path raised the HTTP error
    without writing an audit row, so a stolen-credential attacker hammering
    the verify endpoint left no trace correlatable to the targeted user.

    Durable: ``log_audit`` (not background) — a missing failure row is
    indistinguishable from "no attempt happened" during a forensic review,
    which is exactly the kind of gap incident responders need to close.
    """
    await log_audit(
        actor=AuditActor.API_CLIENT,
        action=AuditAction.MFA_ENROLL if enrolled else AuditAction.MFA_VERIFY,
        target_resource=f"user:{user.id}",
        outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
        metadata={
            "method": method,
            "phase": phase,
            "ip_address": _client_ip(request),
            "purpose": ticket.purpose,
            "error_type": type(error).__name__,
        },
        institution_id=user.institution_id,
        user_id=_audit_user_id(user),
        location_id=_audit_location_id(user),
        request_id=ticket.audit_request_id,
    )


async def _complete_mfa_auth(
    *,
    request: Request,
    response: Response,
    ticket: MfaTicket,
    user: User,
    method: str,
    recovery_codes: list[str] | None = None,
    enrolled: bool = False,
) -> AuthSession:
    client_ip = _client_ip(request)
    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.MFA_ENROLL if enrolled else AuditAction.MFA_VERIFY,
        target_resource=f"user:{user.id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "method": method,
            "ip_address": client_ip,
            "purpose": ticket.purpose,
        },
        institution_id=user.institution_id,
        user_id=_audit_user_id(user),
        location_id=_audit_location_id(user),
        request_id=ticket.audit_request_id,
    )
    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.LOGIN,
        target_resource=f"user:{user.id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "action": "mfa_complete",
            "method": method,
            "ip_address": client_ip,
            "purpose": ticket.purpose,
        },
        institution_id=user.institution_id,
        user_id=_audit_user_id(user),
        location_id=_audit_location_id(user),
        request_id=ticket.audit_request_id,
    )
    try:
        await MfaTicketService.consume(ticket)
    except MfaError as exc:
        raise _mfa_exception_to_http(exc) from exc
    session = await _issue_auth_session(
        user,
        response,
        revoke_existing=ticket.revoke_existing,
        amr=_mfa_amr(method),
    )
    session.recovery_codes = recovery_codes
    return session


@asynccontextmanager
async def _auth_db_session(
    context_type: str = "auth",
    *,
    external_id: str | None = None,
    user_id: str | None = None,
):
    """Open an RLS-authenticated DB session for an auth flow.

    The default 'auth' context narrows on `users.id = app_rls_user_id()`,
    so callers must pass ``user_id`` (e.g. the refresh-session path that
    has already validated the cookie). Lookup-by-something-else paths
    must use one of the per-flow subcontexts and pass the lookup value
    in ``external_id``:

      - 'auth_email'         (login, forgot-password)         → email
      - 'auth_reset_token'   (reset-password)                 → token_hash
      - 'auth_invite_token'  (set-password / accept invite)   → token_hash

    Without this split, RLS gives zero containment for users queries
    that don't yet know user_id — see migration
    20260509_narrow_auth_users_rls.
    """
    with use_rls_context(
        RlsContext.system(context_type, user_id=user_id, external_id=external_id)
    ):
        async with get_db_session() as session:
            yield session


@router.post("/login", response_model=AuthResult)
@limiter.limit(RATE_AUTH)
async def login(request: Request, response: Response, data: LoginRequest) -> AuthResult:
    """Authenticate a user with local email/password credentials."""
    email = _normalize_email(data.email)
    client_ip = _client_ip(request)
    audit_meta: dict[str, str] = {"action": "local_login"}
    if client_ip:
        audit_meta["ip_address"] = client_ip

    async with _auth_db_session("auth_email", external_id=email) as session:
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
            await _register_failed_login_attempt(session, user)
            # Persist the increment BEFORE raising — otherwise get_db_session's
            # rollback-on-exception path undoes it and HIPAA §164.312(d)
            # account lockout never triggers.
            await session.commit()
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
            is_now_locked = await _register_failed_login_attempt(session, user)
            # Persist the increment + locked_until before raising — see
            # account-inactive branch above.
            await session.commit()
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

        mfa_status = await MfaService(session).status_for_user(str(user.id))
        return await _create_mfa_ticket_response(
            request=request,
            user=user,
            mfa_status=mfa_status,
            audit_request_id=audit_request_id,
            purpose="login",
        )


@router.post("/token", response_model=AuthResult)
@limiter.limit(RATE_AUTH)
async def login_oauth_form(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> AuthResult:
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

    user_for_email: User | None = None
    raw_token: str | None = None

    async with _auth_db_session("auth_email", external_id=email) as session:
        result = await session.execute(
            select(User).where(
                User.email == email,
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()

        if user and user.is_active:
            raw_token = PasswordService.generate_one_time_token()
            user.password_reset_token_hash = PasswordService.hash_token(raw_token)
            user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(
                minutes=settings.password_reset_token_ttl_minutes
            )
            # Persist BEFORE attempting email send. If we awaited the email
            # send inside this context and it raised, get_db_session would
            # rollback and the user would lose the token they were issued.
            await session.commit()
            user_for_email = user

    # Send email outside the DB session. Failures (e.g.
    # AUTH_FRONTEND_BASE_URL missing, Resend down) must NOT propagate as
    # 500 — that would let an attacker enumerate valid emails by checking
    # which inputs cause 500 vs 200. The token is already persisted, so a
    # client retry simply re-issues a fresh token.
    if user_for_email is not None and raw_token is not None:
        try:
            await email_service.send_password_reset_email(
                email=user_for_email.email,
                token=raw_token,
                redirect_url=data.redirect_url,
            )
        except Exception as exc:
            logger.error(
                "Failed to send password reset email for user_id=%s: %s",
                user_for_email.id, exc, exc_info=True,
            )

        log_audit_background(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.PASSWORD_RESET_REQUEST,
            target_resource=f"user:{user_for_email.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "action": "forgot_password",
                "ip_address": client_ip,
            },
            institution_id=user_for_email.institution_id,
            user_id=_audit_user_id(user_for_email),
            location_id=_audit_location_id(user_for_email),
        )

    return MessageResponse(
        message="If an account exists for that email, a password reset email has been sent."
    )


@router.post("/reset-password", response_model=AuthResult)
@limiter.limit("20/minute")
async def reset_password(
    request: Request,
    response: Response,
    data: ResetPasswordRequest,
) -> AuthResult:
    """Consume a password reset token, set a new password, and issue a JWT."""
    token_hash = PasswordService.hash_token(data.token)
    client_ip = _client_ip(request)

    async with _auth_db_session("auth_reset_token", external_id=token_hash) as session:
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

        _set_password_or_400(user, data.password)
        _clear_password_reset_state(user)

        # Durable: password reset is a credential change; record it before any
        # session can be issued from the MFA completion step.
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
        mfa_status = await MfaService(session).status_for_user(str(user.id))
        return await _create_mfa_ticket_response(
            request=request,
            user=user,
            mfa_status=mfa_status,
            audit_request_id=str(uuid4()),
            purpose="reset_password",
            revoke_existing=True,
            post_password_action="reset_password",
        )


@router.post("/set-password", response_model=AuthResult)
@limiter.limit("20/minute")
async def set_password(
    request: Request,
    response: Response,
    data: SetPasswordRequest,
) -> AuthResult:
    """Consume an invite token, set a password, and issue a JWT."""
    token_hash = PasswordService.hash_token(data.token)
    client_ip = _client_ip(request)

    async with _auth_db_session("auth_invite_token", external_id=token_hash) as session:
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

        _set_password_or_400(user, data.password)
        user.invite_status = InviteStatus.ACCEPTED.value
        _clear_invite_state(user)
        _clear_password_reset_state(user)

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
        mfa_status = await MfaService(session).status_for_user(str(user.id))
        return await _create_mfa_ticket_response(
            request=request,
            user=user,
            mfa_status=mfa_status,
            audit_request_id=str(uuid4()),
            purpose="set_password",
            revoke_existing=True,
            post_password_action="set_password",
        )


@router.post("/mfa/webauthn/register/options", response_model=WebAuthnOptionsResponse)
@limiter.limit(RATE_AUTH)
async def mfa_webauthn_register_options(
    request: Request,
    data: MfaTicketRequest,
) -> WebAuthnOptionsResponse:
    ticket = await _ticket_from_request(request, data.mfa_ticket)
    async with _auth_db_session(user_id=ticket.user_id) as session:
        user = await _user_for_mfa_ticket(session, ticket)
        mfa = MfaService(session)
        mfa_status = await mfa.status_for_user(str(user.id))
        if mfa_status.enrolled_for_role(user.role):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is already enrolled; verify an existing factor",
            )
        options, challenge = await mfa.generate_webauthn_registration_options(user=user)
        await MfaTicketService.update(
            ticket,
            challenge=challenge,
            challenge_type="webauthn_register",
        )
        log_audit_background(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.MFA_CHALLENGE,
            target_resource=f"user:{user.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={"method": "webauthn", "phase": "register_options"},
            institution_id=user.institution_id,
            user_id=_audit_user_id(user),
            location_id=_audit_location_id(user),
            request_id=ticket.audit_request_id,
        )
        return WebAuthnOptionsResponse(options=options)


@router.post("/mfa/webauthn/register/verify", response_model=AuthSession)
@limiter.limit(RATE_AUTH)
async def mfa_webauthn_register_verify(
    request: Request,
    response: Response,
    data: WebAuthnRegistrationVerifyRequest,
) -> AuthSession:
    ticket = await _ticket_from_request(request, data.mfa_ticket)
    if ticket.challenge_type != "webauthn_register" or not ticket.challenge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passkey registration challenge is missing or expired",
        )

    recovery_codes: list[str] = []
    async with _auth_db_session(user_id=ticket.user_id) as session:
        user = await _user_for_mfa_ticket(session, ticket)
        mfa = MfaService(session)
        mfa_status = await mfa.status_for_user(str(user.id))
        if mfa_status.enrolled_for_role(user.role):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is already enrolled; verify an existing factor",
            )
        try:
            await mfa.verify_webauthn_registration(
                user=user,
                credential=data.credential,
                expected_challenge=ticket.challenge,
                device_label=data.device_label,
            )
            recovery_codes = await mfa.ensure_recovery_codes(user_id=str(user.id))
        except MfaError as exc:
            await _audit_mfa_failure(
                request=request, user=user, ticket=ticket,
                method="webauthn", phase="register_verify",
                error=exc, enrolled=True,
            )
            raise _mfa_exception_to_http(exc) from exc

    return await _complete_mfa_auth(
        request=request,
        response=response,
        ticket=ticket,
        user=user,
        method="webauthn",
        recovery_codes=recovery_codes,
        enrolled=True,
    )


@router.post("/mfa/webauthn/authenticate/options", response_model=WebAuthnOptionsResponse)
@limiter.limit(RATE_AUTH)
async def mfa_webauthn_authenticate_options(
    request: Request,
    data: MfaTicketRequest,
) -> WebAuthnOptionsResponse:
    ticket = await _ticket_from_request(request, data.mfa_ticket)
    async with _auth_db_session(user_id=ticket.user_id) as session:
        user = await _user_for_mfa_ticket(session, ticket)
        mfa = MfaService(session)
        options, challenge = await mfa.generate_webauthn_authentication_options(
            user_id=str(user.id)
        )
        await MfaTicketService.update(
            ticket,
            challenge=challenge,
            challenge_type="webauthn_authenticate",
        )
        log_audit_background(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.MFA_CHALLENGE,
            target_resource=f"user:{user.id}",
            outcome=AuditOutcome.SUCCESS,
            metadata={"method": "webauthn", "phase": "authenticate_options"},
            institution_id=user.institution_id,
            user_id=_audit_user_id(user),
            location_id=_audit_location_id(user),
            request_id=ticket.audit_request_id,
        )
        return WebAuthnOptionsResponse(options=options)


@router.post("/mfa/webauthn/authenticate/verify", response_model=AuthSession)
@limiter.limit(RATE_AUTH)
async def mfa_webauthn_authenticate_verify(
    request: Request,
    response: Response,
    data: WebAuthnAuthenticationVerifyRequest,
) -> AuthSession:
    ticket = await _ticket_from_request(request, data.mfa_ticket)
    if ticket.challenge_type != "webauthn_authenticate" or not ticket.challenge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passkey authentication challenge is missing or expired",
        )

    async with _auth_db_session(user_id=ticket.user_id) as session:
        user = await _user_for_mfa_ticket(session, ticket)
        try:
            await MfaService(session).verify_webauthn_authentication(
                user_id=str(user.id),
                credential=data.credential,
                expected_challenge=ticket.challenge,
            )
        except MfaError as exc:
            await _audit_mfa_failure(
                request=request, user=user, ticket=ticket,
                method="webauthn", phase="authenticate_verify",
                error=exc,
            )
            raise _mfa_exception_to_http(exc) from exc

    return await _complete_mfa_auth(
        request=request,
        response=response,
        ticket=ticket,
        user=user,
        method="webauthn",
    )


@router.post("/mfa/totp/setup/options", response_model=TotpSetupResponse)
@limiter.limit(RATE_AUTH)
async def mfa_totp_setup_options(
    request: Request,
    data: MfaTicketRequest,
) -> TotpSetupResponse:
    ticket = await _ticket_from_request(request, data.mfa_ticket)
    if ticket.role == UserRole.SUPER_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Super admin accounts must enroll a passkey",
        )
    secret = MfaService.new_totp_secret()
    async with _auth_db_session(user_id=ticket.user_id) as session:
        user = await _user_for_mfa_ticket(session, ticket)
        mfa_status = await MfaService(session).status_for_user(str(user.id))
        if mfa_status.enrolled_for_role(user.role):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is already enrolled; verify an existing factor",
            )
        await MfaTicketService.update(
            ticket,
            pending_totp_secret=secret,
            challenge_type="totp_setup",
        )
        return TotpSetupResponse(
            secret=secret,
            provisioning_uri=MfaService.totp_uri(secret=secret, email=user.email),
        )


@router.post("/mfa/totp/setup/verify", response_model=AuthSession)
@limiter.limit(RATE_AUTH)
async def mfa_totp_setup_verify(
    request: Request,
    response: Response,
    data: TotpVerifyRequest,
) -> AuthSession:
    ticket = await _ticket_from_request(request, data.mfa_ticket)
    if ticket.role == UserRole.SUPER_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Super admin accounts must enroll a passkey",
        )
    if ticket.challenge_type != "totp_setup" or not ticket.pending_totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP setup challenge is missing or expired",
        )

    recovery_codes: list[str] = []
    async with _auth_db_session(user_id=ticket.user_id) as session:
        user = await _user_for_mfa_ticket(session, ticket)
        mfa = MfaService(session)
        mfa_status = await mfa.status_for_user(str(user.id))
        if mfa_status.enrolled_for_role(user.role):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is already enrolled; verify an existing factor",
            )
        try:
            await mfa.verify_and_store_totp_setup(
                user_id=str(user.id),
                secret=ticket.pending_totp_secret,
                code=data.code,
            )
            recovery_codes = await mfa.ensure_recovery_codes(user_id=str(user.id))
        except MfaError as exc:
            await _audit_mfa_failure(
                request=request, user=user, ticket=ticket,
                method="totp", phase="setup_verify",
                error=exc, enrolled=True,
            )
            raise _mfa_exception_to_http(exc) from exc

    return await _complete_mfa_auth(
        request=request,
        response=response,
        ticket=ticket,
        user=user,
        method="totp",
        recovery_codes=recovery_codes,
        enrolled=True,
    )


@router.post("/mfa/totp/verify", response_model=AuthSession)
@limiter.limit(RATE_AUTH)
async def mfa_totp_verify(
    request: Request,
    response: Response,
    data: TotpVerifyRequest,
) -> AuthSession:
    ticket = await _ticket_from_request(request, data.mfa_ticket)
    if ticket.role == UserRole.SUPER_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Super admin accounts must use a passkey or recovery code",
        )
    async with _auth_db_session(user_id=ticket.user_id) as session:
        user = await _user_for_mfa_ticket(session, ticket)
        try:
            await MfaService(session).verify_totp(user_id=str(user.id), code=data.code)
        except MfaError as exc:
            await _audit_mfa_failure(
                request=request, user=user, ticket=ticket,
                method="totp", phase="verify",
                error=exc,
            )
            raise _mfa_exception_to_http(exc) from exc

    return await _complete_mfa_auth(
        request=request,
        response=response,
        ticket=ticket,
        user=user,
        method="totp",
    )


@router.post("/mfa/recovery-code/verify", response_model=AuthSession)
@limiter.limit(RATE_AUTH)
async def mfa_recovery_code_verify(
    request: Request,
    response: Response,
    data: RecoveryCodeVerifyRequest,
) -> AuthSession:
    ticket = await _ticket_from_request(request, data.mfa_ticket)
    async with _auth_db_session(user_id=ticket.user_id) as session:
        user = await _user_for_mfa_ticket(session, ticket)
        try:
            await MfaService(session).use_recovery_code(
                user_id=str(user.id),
                code=data.code,
            )
        except MfaError as exc:
            await _audit_mfa_failure(
                request=request, user=user, ticket=ticket,
                method="recovery_code", phase="verify",
                error=exc,
            )
            raise _mfa_exception_to_http(exc) from exc

    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.MFA_RECOVERY_CODE_USE,
        target_resource=f"user:{user.id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"ip_address": _client_ip(request), "purpose": ticket.purpose},
        institution_id=user.institution_id,
        user_id=_audit_user_id(user),
        location_id=_audit_location_id(user),
        request_id=ticket.audit_request_id,
    )
    return await _complete_mfa_auth(
        request=request,
        response=response,
        ticket=ticket,
        user=user,
        method="recovery_code",
    )


@router.get("/mfa/status", response_model=MfaStatusResponse)
@limiter.limit("30/minute")
async def mfa_status(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> MfaStatusResponse:
    async with _auth_db_session(user_id=str(current_user.id)) as session:
        status_value = await MfaService(session).status_for_user(str(current_user.id))
    return MfaStatusResponse(
        webauthn_count=status_value.webauthn_count,
        totp_enabled=status_value.totp_enabled,
        recovery_codes_remaining=status_value.recovery_codes_remaining,
        methods=status_value.available_methods_for_role(current_user.role),
    )


@router.post("/mfa/recovery-codes/regenerate", response_model=RecoveryCodesResponse)
@limiter.limit("10/minute")
async def mfa_recovery_codes_regenerate(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RecoveryCodesResponse:
    async with _auth_db_session(user_id=str(current_user.id)) as session:
        codes = await MfaService(session).replace_recovery_codes(user_id=str(current_user.id))

    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.MFA_RECOVERY_CODES_REGENERATE,
        target_resource=f"user:{current_user.id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"ip_address": _client_ip(request)},
        institution_id=current_user.institution_id,
        user_id=_audit_user_id(current_user),
        location_id=_audit_location_id(current_user),
    )
    return RecoveryCodesResponse(recovery_codes=codes)


# =============================================================================
# Factor management — for lost / stolen / swapped authenticators.
# Removing a factor never bricks the account: if the user ends up with zero
# factors, the next login returns mfa_setup_required and re-enrolls them.
# =============================================================================

@router.get("/mfa/webauthn", response_model=WebAuthnCredentialListResponse)
@limiter.limit("30/minute")
async def mfa_webauthn_list(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> WebAuthnCredentialListResponse:
    """List the user's registered passkeys (metadata only, never the key)."""
    async with _auth_db_session(user_id=str(current_user.id)) as session:
        rows = await MfaService(session).webauthn_credentials(str(current_user.id))
    return WebAuthnCredentialListResponse(
        credentials=[
            WebAuthnCredentialSummary(
                id=row.id,
                device_label=row.device_label,
                aaguid=row.aaguid,
                credential_device_type=row.credential_device_type,
                credential_backed_up=row.credential_backed_up,
                transports=row.transports,
                created_at=row.created_at,
                last_used_at=row.last_used_at,
            )
            for row in rows
        ]
    )


@router.delete("/mfa/webauthn/{credential_pk}", response_model=MessageResponse)
@limiter.limit("20/minute")
async def mfa_webauthn_remove(
    request: Request,
    credential_pk: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> MessageResponse:
    """Remove a passkey owned by the current user. §164.312(b) audited."""
    async with _auth_db_session(user_id=str(current_user.id)) as session:
        removed = await MfaService(session).remove_webauthn_credential(
            user_id=str(current_user.id),
            credential_pk=credential_pk,
        )

    if removed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Passkey not found",
        )

    # Durable: factor removal is a security-relevant credential change.
    # The 401-on-no-factors case is handled at next login; here we only
    # need a clean audit trail of "user X removed credential Y at time T".
    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.MFA_FACTOR_REMOVE,
        target_resource=f"user:{current_user.id}/webauthn:{credential_pk}",
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "method": "webauthn",
            "device_label": removed.device_label,
            "ip_address": _client_ip(request),
        },
        institution_id=current_user.institution_id,
        user_id=_audit_user_id(current_user),
        location_id=_audit_location_id(current_user),
    )
    return MessageResponse(message="Passkey removed")


@router.post("/mfa/totp/disable", response_model=MessageResponse)
@limiter.limit("10/minute")
async def mfa_totp_disable(
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> MessageResponse:
    """Disable the current user's TOTP authenticator. Idempotent."""
    async with _auth_db_session(user_id=str(current_user.id)) as session:
        was_enabled = await MfaService(session).disable_totp(
            user_id=str(current_user.id)
        )

    # Audit only the state-changing case so a quiet retry doesn't generate
    # noise rows. Idempotent no-op (already disabled) is not a security
    # event worth logging — the existing MFA_FACTOR_DISABLE row from the
    # original disable is the durable record.
    if was_enabled:
        await log_audit(
            actor=AuditActor.ADMIN,
            action=AuditAction.MFA_FACTOR_DISABLE,
            target_resource=f"user:{current_user.id}/totp",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "method": "totp",
                "ip_address": _client_ip(request),
            },
            institution_id=current_user.institution_id,
            user_id=_audit_user_id(current_user),
            location_id=_audit_location_id(current_user),
        )
    return MessageResponse(
        message="Authenticator app disabled" if was_enabled else "Authenticator app was not enabled"
    )


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

    refresh_session_data: RefreshSession | None
    try:
        refresh_session_data = await RefreshTokenService.get_session_for_token(refresh_token)
    except RefreshTokenReplayError as replay_err:
        # Replay can be detected at first lookup (rotated set hit) — handle
        # the same way as rotate_token's replay branch: audit + 401.
        replay_user_id = str(replay_err) if str(replay_err) else None
        await log_audit(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.LOGIN,
            target_resource=f"user:{replay_user_id}" if replay_user_id else "auth:refresh",
            outcome=AuditOutcome.FAILURE_UNAUTHORIZED,
            metadata={
                "action": "refresh_token_replay_detected",
                "ip_address": client_ip,
                "stage": "lookup",
            },
            user_id=replay_user_id,
        )
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token replay detected. Please sign in again.",
        )
    except Exception as e:
        logger.error("Failed to validate refresh token: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication session store is unavailable",
        )

    if not refresh_session_data:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    if not refresh_session_data.mfa:
        await RefreshTokenService.revoke_token(refresh_token)
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MFA verification required. Please sign in again.",
        )

    user_id = refresh_session_data.user_id

    async with _auth_db_session(user_id=str(user_id)) as session:
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
        new_refresh_token = await RefreshTokenService.rotate_token(
            user.id,
            refresh_token,
            mfa=True,
            amr=refresh_session_data.amr,
            auth_time=refresh_session_data.auth_time,
        )
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
        access = await _issue_mfa_bound_access_token(
            user,
            amr=refresh_session_data.amr or ("pwd", "mfa"),
            auth_time=refresh_session_data.auth_time
            or int(datetime.now(timezone.utc).timestamp()),
        )
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
