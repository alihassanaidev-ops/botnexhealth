"""Real-stack failure paths for MFA against Postgres + Redis testcontainers.

The happy path is exercised by ``test_mfa_real_stack.py``. This file
pins the negative-space contracts that, when broken, would let an
attacker (or a coding mistake) bypass MFA:

- A used TOTP time step cannot be replayed within the ±1-step window.
- A consumed MFA ticket cannot be re-used.
- SUPER_ADMIN cannot enroll TOTP (passkey-only policy).
- A user already enrolled cannot re-register without removing the
  existing factor first (defends against ticket-reuse-during-enroll).
- A recovery code is single-use; presenting it twice fails the second
  attempt and writes a §164.312(b) failure audit row.

The fixtures (postgres_url, redis_url, real_stack, _seed_user) live in
``test_mfa_real_stack.py`` and are imported here so the same Postgres /
Redis containers are reused across both files.
"""

from __future__ import annotations

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.app.config import settings
from src.app.database import get_system_db_session
from src.app.main import app
from src.app.models.audit_log import AuditAction, AuditLog, AuditOutcome
from src.app.models.user import UserRole
from src.app.services.audit import (
    AuditService,
    PostgresAuditRepository,
    set_audit_service,
)

# Import fixtures from the happy-path file so we share the testcontainers.
from tests.integration.test_mfa_real_stack import (  # noqa: F401
    migrated_postgres,
    postgres_url,
    real_stack,
    redis_url,
    _seed_user,
)


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def real_audit_repository():
    """The conftest installs an in-memory audit repo for every test
    (autouse=True). For real-stack tests we need rows to actually land
    in the audit_logs Postgres table so we can query them back."""
    set_audit_service(AuditService(PostgresAuditRepository()))
    yield


@pytest.fixture(autouse=True)
def reset_rate_limiter_between_tests():
    """Real-stack tests all hit the app from 127.0.0.1, so the
    ``RATE_AUTH = "10/minute"`` limiter on /api/auth/login eventually
    starts returning 429. Reset the in-memory limiter state between
    tests so each test sees a clean rate-limit budget."""
    from src.app.api.rate_limit import limiter
    limiter.reset()
    yield
    limiter.reset()


# =============================================================================
# TOTP replay within the same time step
# =============================================================================

@pytest.mark.asyncio
async def test_totp_code_cannot_be_replayed_within_same_time_step(real_stack):
    """A successful TOTP verify advances last_accepted_time_step. Replaying
    the same 6-digit code (which is valid for ~30s) must fail the second
    time even though it's still inside the validity window."""
    user_id, _, _ = await _seed_user(
        "totp-replay@example.com", role=UserRole.INSTITUTION_ADMIN.value
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Login → enroll TOTP.
        login = await client.post(
            "/api/auth/login",
            json={"email": "totp-replay@example.com", "password": "ValidPass123!"},
        )
        ticket = login.json()["mfa_ticket"]
        secret = (await client.post(
            "/api/auth/mfa/totp/setup/options", json={"mfa_ticket": ticket},
        )).json()["secret"]
        code = pyotp.TOTP(secret).now()
        verify = await client.post(
            "/api/auth/mfa/totp/setup/verify",
            json={"mfa_ticket": ticket, "code": code},
        )
        assert verify.status_code == 200, verify.text
        client.cookies.clear()

        # 2. Re-login + present the *same* code on a fresh ticket.
        relogin = await client.post(
            "/api/auth/login",
            json={"email": "totp-replay@example.com", "password": "ValidPass123!"},
        )
        replay_ticket = relogin.json()["mfa_ticket"]
        replay = await client.post(
            "/api/auth/mfa/totp/verify",
            json={"mfa_ticket": replay_ticket, "code": code},
        )
        assert replay.status_code == 401, replay.text
        # The error text intentionally distinguishes "already used" so the
        # client can prompt the user to wait for the next 30-second window.
        assert "already used" in replay.json()["detail"].lower()


# =============================================================================
# MFA ticket is single-use
# =============================================================================

@pytest.mark.asyncio
async def test_mfa_ticket_cannot_be_reused_after_successful_verify(real_stack):
    """The login ticket is consumed inside ``_complete_mfa_auth``. A
    successful flow must not leave the ticket usable for a second
    verify (e.g., to mint a second access token without re-auth)."""
    user_id, _, _ = await _seed_user(
        "ticket-reuse@example.com", role=UserRole.INSTITUTION_ADMIN.value
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "ticket-reuse@example.com", "password": "ValidPass123!"},
        )
        ticket = login.json()["mfa_ticket"]
        secret = (await client.post(
            "/api/auth/mfa/totp/setup/options", json={"mfa_ticket": ticket},
        )).json()["secret"]
        first = await client.post(
            "/api/auth/mfa/totp/setup/verify",
            json={"mfa_ticket": ticket, "code": pyotp.TOTP(secret).now()},
        )
        assert first.status_code == 200

        # Replay the consumed ticket. Must fail at ticket lookup, not at
        # TOTP verify, since the ticket was deleted from Redis on success.
        replay = await client.post(
            "/api/auth/mfa/totp/verify",
            json={"mfa_ticket": ticket, "code": pyotp.TOTP(secret).now()},
        )
        assert replay.status_code == 401, replay.text


# =============================================================================
# SUPER_ADMIN policy: passkey only
# =============================================================================

@pytest.mark.asyncio
async def test_super_admin_cannot_setup_totp(real_stack):
    """Per the SUPER_ADMIN policy, TOTP is not an offered factor at all.
    /mfa/totp/setup/options returns 400 with the policy explanation."""
    await _seed_user("super@example.com", role=UserRole.SUPER_ADMIN.value)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "super@example.com", "password": "ValidPass123!"},
        )
        body = login.json()
        assert body["status"] == "mfa_setup_required"
        # Server only advertises passkey for super-admins.
        assert body["setup_methods"] == ["webauthn"]

        attempt = await client.post(
            "/api/auth/mfa/totp/setup/options",
            json={"mfa_ticket": body["mfa_ticket"]},
        )
        assert attempt.status_code == 400, attempt.text
        assert "passkey" in attempt.json()["detail"].lower()


# =============================================================================
# Already-enrolled user can't re-register without removing the existing factor
# =============================================================================

@pytest.mark.asyncio
async def test_enrolled_user_cannot_register_a_second_passkey_via_setup_flow(
    real_stack,
):
    """The setup flow is for first-time enrollment. Once the user has any
    factor, /mfa/webauthn/register/options must reject — otherwise an
    attacker who hijacks an MFA ticket could silently add a passkey
    they control. (Adding *additional* passkeys for a logged-in user
    happens via authenticated management endpoints, not the setup flow.)"""
    user_id, _, _ = await _seed_user(
        "enrolled@example.com", role=UserRole.INSTITUTION_ADMIN.value
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "enrolled@example.com", "password": "ValidPass123!"},
        )
        ticket = login.json()["mfa_ticket"]
        secret = (await client.post(
            "/api/auth/mfa/totp/setup/options", json={"mfa_ticket": ticket},
        )).json()["secret"]
        verify = await client.post(
            "/api/auth/mfa/totp/setup/verify",
            json={"mfa_ticket": ticket, "code": pyotp.TOTP(secret).now()},
        )
        assert verify.status_code == 200
        client.cookies.clear()

        # 2. Re-login → MFA challenge → attempt to register a passkey via
        # the setup flow. The user is already enrolled (TOTP), so the
        # challenge must steer them to the verify flow, not setup.
        relogin = await client.post(
            "/api/auth/login",
            json={"email": "enrolled@example.com", "password": "ValidPass123!"},
        )
        body = relogin.json()
        assert body["status"] == "mfa_required", body
        # Setup methods empty when already enrolled.
        assert body["setup_methods"] == []

        register_attempt = await client.post(
            "/api/auth/mfa/webauthn/register/options",
            json={"mfa_ticket": body["mfa_ticket"]},
        )
        assert register_attempt.status_code == 400
        assert "already enrolled" in register_attempt.json()["detail"].lower()


# =============================================================================
# Recovery code is single-use
# =============================================================================

@pytest.mark.asyncio
async def test_recovery_code_consumed_exactly_once_with_audit(real_stack):
    """A recovery code presented twice succeeds once and fails once.
    The failed second attempt writes an MFA_VERIFY/FAILURE_UNAUTHORIZED
    audit row to the real audit_logs table (HIPAA §164.312(b))."""
    user_id, _, _ = await _seed_user(
        "recovery-once@example.com", role=UserRole.INSTITUTION_ADMIN.value
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": "recovery-once@example.com", "password": "ValidPass123!"},
        )
        ticket = login.json()["mfa_ticket"]
        secret = (await client.post(
            "/api/auth/mfa/totp/setup/options", json={"mfa_ticket": ticket},
        )).json()["secret"]
        enroll = await client.post(
            "/api/auth/mfa/totp/setup/verify",
            json={"mfa_ticket": ticket, "code": pyotp.TOTP(secret).now()},
        )
        recovery_codes = enroll.json()["recovery_codes"]
        assert len(recovery_codes) == 10
        target_code = recovery_codes[0]
        client.cookies.clear()

        # 1. First use — succeeds.
        relogin1 = await client.post(
            "/api/auth/login",
            json={"email": "recovery-once@example.com", "password": "ValidPass123!"},
        )
        first = await client.post(
            "/api/auth/mfa/recovery-code/verify",
            json={"mfa_ticket": relogin1.json()["mfa_ticket"], "code": target_code},
        )
        assert first.status_code == 200, first.text
        client.cookies.clear()

        # 2. Second use of the *same* code — must fail.
        relogin2 = await client.post(
            "/api/auth/login",
            json={"email": "recovery-once@example.com", "password": "ValidPass123!"},
        )
        second = await client.post(
            "/api/auth/mfa/recovery-code/verify",
            json={"mfa_ticket": relogin2.json()["mfa_ticket"], "code": target_code},
        )
        assert second.status_code == 401, second.text

    # Drain background audit + assert the failure row persisted.
    await AuditService.drain_background_tasks()
    async with get_system_db_session("audit") as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.MFA_VERIFY.value,
                AuditLog.outcome == AuditOutcome.FAILURE_UNAUTHORIZED.value,
                AuditLog.user_id == user_id,
            )
        )
        rows = list(result.scalars().all())
    assert len(rows) == 1, f"Expected exactly one failure audit row, got {len(rows)}"
    assert rows[0].audit_metadata.get("method") == "recovery_code"
