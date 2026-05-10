"""HIPAA §164.312(b): failed MFA verifications must be audited.

Stolen-credential attackers hammering a verify endpoint used to leave
no audit trail correlatable to the targeted user — the route caught
``MfaVerificationFailed`` and returned 401 without writing a row.
This test pins the new contract for each verify endpoint:
the audit log carries an MFA_VERIFY (or MFA_ENROLL) row with
outcome=FAILURE_UNAUTHORIZED on every failed attempt.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.routes import auth as auth_routes
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.mfa import MfaTicket, MfaVerificationFailed
from src.app.services.password_service import PasswordService


@pytest.fixture
def mfa_user() -> User:
    return User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="22222222-2222-2222-2222-222222222222",
        location_id=None,
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
        password_hash=PasswordService.hash_password("ValidPass123!"),
    )


def _ticket_for(user: User, *, challenge_type: str | None = None, secret: str | None = None) -> MfaTicket:
    return MfaTicket(
        token="mfa-ticket-test",
        user_id=str(user.id),
        purpose="login",
        role=user.role,
        institution_id=user.institution_id,
        location_id=user.location_id,
        audit_request_id="aud-req-test",
        challenge="dGVzdA==",  # base64url("test")
        challenge_type=challenge_type,
        pending_totp_secret=secret,
    )


def _mock_session_returns(user: User):
    """Return a mock session whose User select resolves to ``user``."""
    session = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = user
    session.execute.return_value = res
    return session


def _audit_for(entries, action: AuditAction, outcome: AuditOutcome):
    matches = [
        e for e in entries
        if e.action == action and e.outcome == outcome
    ]
    return matches


@pytest.mark.asyncio
async def test_totp_verify_failure_writes_audit_row(
    async_client: AsyncClient, mfa_user: User, audit_log_entries
):
    """Wrong TOTP code → 401 + MFA_VERIFY/FAILURE_UNAUTHORIZED audit row."""
    ticket = _ticket_for(mfa_user)

    mock_session = _mock_session_returns(mfa_user)
    with patch.object(auth_routes, "_ticket_from_request", new=AsyncMock(return_value=ticket)), \
         patch.object(auth_routes, "get_db_session") as mock_get_db, \
         patch.object(
             auth_routes.MfaService,
             "verify_totp",
             new=AsyncMock(side_effect=MfaVerificationFailed("Invalid authenticator code")),
         ):
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/mfa/totp/verify",
            json={"mfa_ticket": "mfa-ticket-test", "code": "000000"},
        )

    assert response.status_code == 401
    entries = await audit_log_entries()
    failures = _audit_for(entries, AuditAction.MFA_VERIFY, AuditOutcome.FAILURE_UNAUTHORIZED)
    assert len(failures) == 1, [e for e in entries]
    row = failures[0]
    assert row.target_resource == f"user:{mfa_user.id}"
    assert row.user_id == mfa_user.id
    assert row.metadata["method"] == "totp"
    assert row.metadata["phase"] == "verify"
    assert row.metadata["error_type"] == "MfaVerificationFailed"


@pytest.mark.asyncio
async def test_recovery_code_verify_failure_writes_audit_row(
    async_client: AsyncClient, mfa_user: User, audit_log_entries
):
    ticket = _ticket_for(mfa_user)
    mock_session = _mock_session_returns(mfa_user)
    with patch.object(auth_routes, "_ticket_from_request", new=AsyncMock(return_value=ticket)), \
         patch.object(auth_routes, "get_db_session") as mock_get_db, \
         patch.object(
             auth_routes.MfaService,
             "use_recovery_code",
             new=AsyncMock(side_effect=MfaVerificationFailed("Invalid recovery code")),
         ):
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/mfa/recovery-code/verify",
            json={"mfa_ticket": "mfa-ticket-test", "code": "AAAAA-BBBBB-CCCCC"},
        )

    assert response.status_code == 401
    entries = await audit_log_entries()
    failures = _audit_for(entries, AuditAction.MFA_VERIFY, AuditOutcome.FAILURE_UNAUTHORIZED)
    assert len(failures) == 1
    assert failures[0].metadata["method"] == "recovery_code"


@pytest.mark.asyncio
async def test_webauthn_authenticate_failure_writes_audit_row(
    async_client: AsyncClient, mfa_user: User, audit_log_entries
):
    ticket = _ticket_for(mfa_user, challenge_type="webauthn_authenticate")
    mock_session = _mock_session_returns(mfa_user)
    with patch.object(auth_routes, "_ticket_from_request", new=AsyncMock(return_value=ticket)), \
         patch.object(auth_routes, "get_db_session") as mock_get_db, \
         patch.object(
             auth_routes.MfaService,
             "verify_webauthn_authentication",
             new=AsyncMock(side_effect=MfaVerificationFailed("Passkey verification failed")),
         ):
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/mfa/webauthn/authenticate/verify",
            json={
                "mfa_ticket": "mfa-ticket-test",
                "credential": {"id": "abc"},
            },
        )

    assert response.status_code == 401
    entries = await audit_log_entries()
    failures = _audit_for(entries, AuditAction.MFA_VERIFY, AuditOutcome.FAILURE_UNAUTHORIZED)
    assert len(failures) == 1
    assert failures[0].metadata["method"] == "webauthn"


@pytest.mark.asyncio
async def test_totp_setup_verify_failure_writes_enroll_audit_row(
    async_client: AsyncClient, mfa_user: User, audit_log_entries
):
    """Setup-verify failure must audit as MFA_ENROLL (not MFA_VERIFY)."""
    ticket = _ticket_for(
        mfa_user,
        challenge_type="totp_setup",
        secret="JBSWY3DPEHPK3PXP",
    )
    mock_session = _mock_session_returns(mfa_user)

    # Status returns "no factors yet" so the endpoint reaches the verify step.
    from src.app.services.mfa import MfaStatus

    with patch.object(auth_routes, "_ticket_from_request", new=AsyncMock(return_value=ticket)), \
         patch.object(auth_routes, "get_db_session") as mock_get_db, \
         patch.object(
             auth_routes.MfaService,
             "status_for_user",
             new=AsyncMock(return_value=MfaStatus(0, False, 0)),
         ), \
         patch.object(
             auth_routes.MfaService,
             "verify_and_store_totp_setup",
             new=AsyncMock(side_effect=MfaVerificationFailed("Invalid authenticator code")),
         ):
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/mfa/totp/setup/verify",
            json={"mfa_ticket": "mfa-ticket-test", "code": "000000"},
        )

    assert response.status_code == 401
    entries = await audit_log_entries()
    enroll_failures = _audit_for(entries, AuditAction.MFA_ENROLL, AuditOutcome.FAILURE_UNAUTHORIZED)
    assert len(enroll_failures) == 1, [e for e in entries]
    assert enroll_failures[0].metadata["method"] == "totp"
    assert enroll_failures[0].metadata["phase"] == "setup_verify"


@pytest.mark.asyncio
async def test_webauthn_register_verify_failure_writes_enroll_audit_row(
    async_client: AsyncClient, mfa_user: User, audit_log_entries
):
    ticket = _ticket_for(mfa_user, challenge_type="webauthn_register")
    mock_session = _mock_session_returns(mfa_user)

    from src.app.services.mfa import MfaStatus

    with patch.object(auth_routes, "_ticket_from_request", new=AsyncMock(return_value=ticket)), \
         patch.object(auth_routes, "get_db_session") as mock_get_db, \
         patch.object(
             auth_routes.MfaService,
             "status_for_user",
             new=AsyncMock(return_value=MfaStatus(0, False, 0)),
         ), \
         patch.object(
             auth_routes.MfaService,
             "verify_webauthn_registration",
             new=AsyncMock(side_effect=MfaVerificationFailed("Passkey registration failed")),
         ):
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            "/api/auth/mfa/webauthn/register/verify",
            json={
                "mfa_ticket": "mfa-ticket-test",
                "credential": {"id": "abc", "response": {}},
                "device_label": "Laptop",
            },
        )

    assert response.status_code == 401
    entries = await audit_log_entries()
    enroll_failures = _audit_for(entries, AuditAction.MFA_ENROLL, AuditOutcome.FAILURE_UNAUTHORIZED)
    assert len(enroll_failures) == 1
    assert enroll_failures[0].metadata["method"] == "webauthn"
    assert enroll_failures[0].metadata["phase"] == "register_verify"
