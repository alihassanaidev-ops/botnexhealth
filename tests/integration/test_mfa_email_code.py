"""Email sign-in codes — the third login MFA method.

A user who has already enrolled a passkey or authenticator app can ask for
a one-time code to be mailed to the account's own address and use it in
place of that factor at login. The tests here pin the boundaries that make
that safe to offer:

  - POST /auth/mfa/email/send    issues a code, throttled three ways
  - POST /auth/mfa/email/verify  redeems it for a session

  - It is never an enrollment method: a user with no real factor is
    refused, so signup still routes through mfa_setup_required.
  - It is never accepted for step-up: there is no step-up email verifier,
    and the step-up challenge does not advertise the method. Otherwise
    mailbox access alone could delete the passkeys it stands in for.
  - It is not offered to SUPER_ADMIN in production.
  - Guessing is bounded: exceeding the attempt cap tears down the login
    ticket rather than just failing the request.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.routes import auth as auth_routes
from src.app.config import settings
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.mfa import (
    MFA_PURPOSE_LOGIN,
    MfaEmailCodeService,
    MfaEmailCodeThrottled,
    MfaStatus,
    MfaTicket,
)
from src.app.services.password_service import PasswordService


ENROLLED = MfaStatus(webauthn_count=0, totp_enabled=True, recovery_codes_remaining=10)
UNENROLLED = MfaStatus(webauthn_count=0, totp_enabled=False, recovery_codes_remaining=0)


@pytest.fixture
def staff_user() -> User:
    return User(
        id="11111111-1111-1111-1111-111111111111",
        email="staff@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="22222222-2222-2222-2222-222222222222",
        location_id=None,
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
        password_hash=PasswordService.hash_password("ValidPass123!"),
    )


@pytest.fixture
def stub_db_session():
    @asynccontextmanager
    async def _noop_session(*_args, **_kwargs):
        yield AsyncMock()

    with patch.object(auth_routes, "_auth_db_session", _noop_session):
        yield


def _ticket(
    user_id: str,
    *,
    role: str = UserRole.INSTITUTION_ADMIN.value,
    code_hash: str | None = None,
    expires_at: int | None = None,
) -> MfaTicket:
    return MfaTicket(
        token="login-token",
        user_id=user_id,
        purpose=MFA_PURPOSE_LOGIN,
        role=role,
        institution_id="22222222-2222-2222-2222-222222222222",
        location_id=None,
        audit_request_id="audit-req-1",
        email_code_hash=code_hash,
        email_code_expires_at=expires_at,
    )


@asynccontextmanager
async def _send_harness(
    *,
    ticket: MfaTicket,
    user: User,
    mfa_status: MfaStatus = ENROLLED,
    send_effect=None,
):
    """Patch everything /mfa/email/send touches, yielding the mock bundle."""
    email_service = AsyncMock()
    email_service.send_login_code_email = AsyncMock(side_effect=send_effect)
    with patch.object(
        auth_routes, "_ticket_from_request", new=AsyncMock(return_value=ticket)
    ), patch.object(
        auth_routes, "_user_for_mfa_ticket", new=AsyncMock(return_value=user)
    ), patch.object(
        auth_routes.MfaService,
        "status_for_user",
        new=AsyncMock(return_value=mfa_status),
    ), patch.object(
        auth_routes.MfaEmailCodeService, "claim_send_slot", new=AsyncMock()
    ) as claim, patch.object(
        auth_routes.MfaEmailCodeService, "bump_send_count", new=AsyncMock(return_value=1)
    ) as bump, patch.object(
        auth_routes.MfaTicketService, "update", new=AsyncMock(return_value=ticket)
    ) as update, patch.object(
        auth_routes, "AuthEmailService", return_value=email_service
    ):
        yield {
            "claim": claim,
            "bump": bump,
            "update": update,
            "send": email_service.send_login_code_email,
        }


# =============================================================================
# POST /mfa/email/send
# =============================================================================


@pytest.mark.asyncio
async def test_send_mails_a_code_and_stashes_only_its_hash(
    async_client: AsyncClient, staff_user: User, stub_db_session, audit_log_entries
):
    ticket = _ticket(staff_user.id)
    async with _send_harness(ticket=ticket, user=staff_user) as mocks:
        response = await async_client.post(
            "/api/auth/mfa/email/send", json={"mfa_ticket": "login-token"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "sent"
    assert body["expires_in_seconds"] == settings.mfa_email_code_ttl_seconds
    assert body["resend_after_seconds"] == settings.mfa_email_code_resend_seconds
    # The code itself never appears in the HTTP response.
    assert "code" not in body

    mailed_code = mocks["send"].await_args.kwargs["code"]
    assert len(mailed_code) == 6 and mailed_code.isdigit()
    assert mocks["send"].await_args.kwargs["email"] == staff_user.email

    # Only the Argon2id hash reaches Redis, alongside its own deadline —
    # the plaintext is never persisted anywhere.
    stored = mocks["update"].await_args.kwargs
    assert mailed_code not in stored["email_code_hash"]
    assert MfaEmailCodeService.verify_code(
        code=mailed_code, code_hash=stored["email_code_hash"]
    )
    assert stored["email_code_expires_at"] > 0

    entries = await audit_log_entries()
    challenges = [e for e in entries if e.action == AuditAction.MFA_CHALLENGE.value]
    assert challenges, "a mailed code is a security event and must be audited"
    assert challenges[-1].metadata["method"] == "email"
    assert challenges[-1].metadata["phase"] == "email_code_send"


@pytest.mark.asyncio
async def test_send_refuses_a_user_with_no_enrolled_factor(
    async_client: AsyncClient, staff_user: User, stub_db_session
):
    """The whole point of the guard: email is an alternative, not a bypass."""
    ticket = _ticket(staff_user.id)
    async with _send_harness(
        ticket=ticket, user=staff_user, mfa_status=UNENROLLED
    ) as mocks:
        response = await async_client.post(
            "/api/auth/mfa/email/send", json={"mfa_ticket": "login-token"}
        )

    assert response.status_code == 400
    assert "enrolled passkey or authenticator app" in response.json()["detail"]
    mocks["send"].assert_not_awaited()
    mocks["claim"].assert_not_awaited()


@pytest.mark.asyncio
async def test_send_refuses_super_admin_in_production(
    async_client: AsyncClient, staff_user: User, stub_db_session, monkeypatch
):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "dev_allow_super_admin_totp", True)
    staff_user.role = UserRole.SUPER_ADMIN.value
    ticket = _ticket(staff_user.id, role=UserRole.SUPER_ADMIN.value)

    async with _send_harness(ticket=ticket, user=staff_user) as mocks:
        response = await async_client.post(
            "/api/auth/mfa/email/send", json={"mfa_ticket": "login-token"}
        )

    assert response.status_code == 400
    mocks["send"].assert_not_awaited()


@pytest.mark.asyncio
async def test_send_honours_the_kill_switch(
    async_client: AsyncClient, staff_user: User, stub_db_session, monkeypatch
):
    monkeypatch.setattr(settings, "mfa_email_code_enabled", False)
    ticket = _ticket(staff_user.id)

    async with _send_harness(ticket=ticket, user=staff_user) as mocks:
        response = await async_client.post(
            "/api/auth/mfa/email/send", json={"mfa_ticket": "login-token"}
        )

    assert response.status_code == 400
    mocks["send"].assert_not_awaited()


@pytest.mark.asyncio
async def test_send_reports_the_cooldown_with_retry_after(
    async_client: AsyncClient, staff_user: User, stub_db_session
):
    ticket = _ticket(staff_user.id)
    async with _send_harness(ticket=ticket, user=staff_user) as mocks:
        mocks["claim"].side_effect = MfaEmailCodeThrottled(
            "A code was just sent", retry_after_seconds=42
        )
        response = await async_client.post(
            "/api/auth/mfa/email/send", json={"mfa_ticket": "login-token"}
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"
    mocks["send"].assert_not_awaited()


@pytest.mark.asyncio
async def test_send_surfaces_a_provider_failure(
    async_client: AsyncClient, staff_user: User, stub_db_session
):
    """Unlike /forgot-password, this one must not fail silently.

    The caller already proved the password, so there is no address to
    enumerate — and swallowing the error would leave the user staring at a
    code entry box waiting for mail that never arrives.
    """
    ticket = _ticket(staff_user.id)
    async with _send_harness(
        ticket=ticket, user=staff_user, send_effect=RuntimeError("resend down")
    ):
        response = await async_client.post(
            "/api/auth/mfa/email/send", json={"mfa_ticket": "login-token"}
        )

    assert response.status_code == 502
    assert "another method" in response.json()["detail"]


# =============================================================================
# POST /mfa/email/verify
# =============================================================================


@asynccontextmanager
async def _verify_harness(
    *,
    ticket: MfaTicket,
    user: User,
    mfa_status: MfaStatus = ENROLLED,
    attempt_effect=None,
):
    with patch.object(
        auth_routes, "_ticket_from_request", new=AsyncMock(return_value=ticket)
    ), patch.object(
        auth_routes, "_user_for_mfa_ticket", new=AsyncMock(return_value=user)
    ), patch.object(
        auth_routes.MfaService,
        "status_for_user",
        new=AsyncMock(return_value=mfa_status),
    ), patch.object(
        auth_routes.MfaEmailCodeService,
        "bump_attempt_count",
        new=AsyncMock(side_effect=attempt_effect, return_value=1),
    ) as attempts, patch.object(
        auth_routes.MfaEmailCodeService, "clear_counters", new=AsyncMock()
    ) as clear, patch.object(
        auth_routes.MfaTicketService, "consume", new=AsyncMock()
    ) as consume, patch.object(
        auth_routes,
        "_complete_mfa_auth",
        new=AsyncMock(
            return_value=auth_routes.AuthSession(
                access_token="issued-token", token_type="bearer"
            )
        ),
    ) as complete:
        yield {
            "attempts": attempts,
            "clear": clear,
            "consume": consume,
            "complete": complete,
        }


def _pending(user_id: str, code: str) -> MfaTicket:
    import time

    return _ticket(
        user_id,
        code_hash=MfaEmailCodeService.hash_code(code),
        expires_at=int(time.time()) + 300,
    )


@pytest.mark.asyncio
async def test_verify_accepts_the_mailed_code_and_issues_a_session(
    async_client: AsyncClient, staff_user: User, stub_db_session
):
    ticket = _pending(staff_user.id, "481902")
    async with _verify_harness(ticket=ticket, user=staff_user) as mocks:
        response = await async_client.post(
            "/api/auth/mfa/email/verify",
            json={"mfa_ticket": "login-token", "code": "481902"},
        )

    assert response.status_code == 200
    assert response.json()["access_token"] == "issued-token"

    # The session records how it was authenticated, so a later review can
    # tell an email-code sign-in from a passkey one.
    assert mocks["complete"].await_args.kwargs["method"] == "email"
    mocks["clear"].assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_rejects_a_wrong_code_and_audits_it(
    async_client: AsyncClient, staff_user: User, stub_db_session, audit_log_entries
):
    ticket = _pending(staff_user.id, "481902")
    async with _verify_harness(ticket=ticket, user=staff_user) as mocks:
        response = await async_client.post(
            "/api/auth/mfa/email/verify",
            json={"mfa_ticket": "login-token", "code": "000000"},
        )

    assert response.status_code == 401
    mocks["complete"].assert_not_awaited()
    # The ticket survives a single wrong guess — the user gets to retry.
    mocks["consume"].assert_not_awaited()

    entries = await audit_log_entries()
    failures = [
        e
        for e in entries
        if e.action == AuditAction.MFA_VERIFY.value
        and e.outcome == AuditOutcome.FAILURE_UNAUTHORIZED.value
    ]
    assert failures and failures[-1].metadata["method"] == "email"


@pytest.mark.asyncio
async def test_verify_rejects_an_expired_code(
    async_client: AsyncClient, staff_user: User, stub_db_session
):
    import time

    ticket = _ticket(
        staff_user.id,
        code_hash=MfaEmailCodeService.hash_code("481902"),
        expires_at=int(time.time()) - 1,
    )
    async with _verify_harness(ticket=ticket, user=staff_user) as mocks:
        response = await async_client.post(
            "/api/auth/mfa/email/verify",
            json={"mfa_ticket": "login-token", "code": "481902"},
        )

    assert response.status_code == 400
    assert "expired" in response.json()["detail"]
    mocks["complete"].assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_rejects_when_no_code_was_ever_sent(
    async_client: AsyncClient, staff_user: User, stub_db_session
):
    async with _verify_harness(ticket=_ticket(staff_user.id), user=staff_user) as mocks:
        response = await async_client.post(
            "/api/auth/mfa/email/verify",
            json={"mfa_ticket": "login-token", "code": "481902"},
        )

    assert response.status_code == 400
    mocks["complete"].assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_tears_down_the_login_when_the_attempt_cap_is_hit(
    async_client: AsyncClient, staff_user: User, stub_db_session
):
    """Exceeding the cap must burn the ticket, not merely fail the request.

    A 6-digit code is a 1e6 space; only restarting from the password
    resets the budget, and that re-proves the first factor.
    """
    ticket = _pending(staff_user.id, "481902")
    async with _verify_harness(
        ticket=ticket,
        user=staff_user,
        attempt_effect=MfaEmailCodeThrottled("Too many incorrect codes"),
    ) as mocks:
        response = await async_client.post(
            "/api/auth/mfa/email/verify",
            # Even the *correct* code is refused once the budget is spent.
            json={"mfa_ticket": "login-token", "code": "481902"},
        )

    assert response.status_code == 401
    mocks["consume"].assert_awaited_once()
    mocks["clear"].assert_awaited_once()
    mocks["complete"].assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_counts_the_attempt_before_checking_the_code(
    async_client: AsyncClient, staff_user: User, stub_db_session
):
    """Otherwise an early return would hand out free guesses."""
    async with _verify_harness(ticket=_ticket(staff_user.id), user=staff_user) as mocks:
        await async_client.post(
            "/api/auth/mfa/email/verify",
            json={"mfa_ticket": "login-token", "code": "481902"},
        )

    # No code was pending, so the request 400s — but the guess still counted.
    mocks["attempts"].assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_refuses_a_user_whose_factor_was_removed_mid_flow(
    async_client: AsyncClient, staff_user: User, stub_db_session
):
    ticket = _pending(staff_user.id, "481902")
    async with _verify_harness(
        ticket=ticket, user=staff_user, mfa_status=UNENROLLED
    ) as mocks:
        response = await async_client.post(
            "/api/auth/mfa/email/verify",
            json={"mfa_ticket": "login-token", "code": "481902"},
        )

    assert response.status_code == 400
    mocks["complete"].assert_not_awaited()


# =============================================================================
# Step-up must not learn about this method
# =============================================================================


@pytest.mark.asyncio
async def test_no_step_up_email_endpoint_exists():
    """The absence is the control, so assert it rather than assume it."""
    paths = {route.path for route in auth_routes.router.routes}
    assert not any("step-up" in path and "email" in path for path in paths)


@pytest.mark.asyncio
async def test_step_up_challenge_does_not_advertise_email(
    async_client: AsyncClient, staff_user: User, stub_db_session
):
    from src.app.api.deps import get_current_active_user
    from src.app.main import app

    app.dependency_overrides[get_current_active_user] = lambda: staff_user
    try:
        with patch.object(
            auth_routes.MfaService,
            "status_for_user",
            new=AsyncMock(return_value=ENROLLED),
        ), patch.object(
            auth_routes.MfaTicketService,
            "create",
            new=AsyncMock(return_value="step-up-token"),
        ):
            response = await async_client.post("/api/auth/mfa/step-up/challenge")
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)

    assert response.status_code == 200
    methods = response.json()["methods"]
    assert "email" not in methods
    # The stronger factors are still offered.
    assert "totp" in methods and "recovery_code" in methods
