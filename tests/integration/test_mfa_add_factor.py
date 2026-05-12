"""Add-factor endpoints — enrol an additional passkey or TOTP via the
Security settings page while already signed in.

Differences from the initial-setup flow:
  - Step-up MFA gate (not login MFA gate).
  - No ``enrolled_for_role`` check — the whole point is to add factors
    on an already-enrolled account.
  - No AuthSession returned; the caller already has a session.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import get_current_active_user
from src.app.api.routes import auth as auth_routes
from src.app.main import app
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.mfa import UserTotpFactor, WebAuthnCredential
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.mfa import (
    MFA_PURPOSE_ADD_FACTOR_TOTP,
    MFA_PURPOSE_ADD_FACTOR_WEBAUTHN,
    MFA_PURPOSE_STEP_UP,
    MfaStatus,
    MfaTicket,
    MfaTicketInvalid,
)
from src.app.services.password_service import PasswordService


@pytest.fixture
def authenticated_user() -> User:
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


@pytest.fixture
def override_current_user(authenticated_user):
    app.dependency_overrides[get_current_active_user] = lambda: authenticated_user
    try:
        yield authenticated_user
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)


@pytest.fixture
def stub_db_session():
    @asynccontextmanager
    async def _noop(*_args, **_kwargs):
        yield AsyncMock()

    with patch.object(auth_routes, "_auth_db_session", _noop):
        yield


def _step_up_ticket(user_id: str) -> MfaTicket:
    return MfaTicket(
        token="step-up-token",
        user_id=user_id,
        purpose=MFA_PURPOSE_STEP_UP,
        role="INSTITUTION_ADMIN",
        institution_id="22222222-2222-2222-2222-222222222222",
        location_id=None,
        audit_request_id="aud-1",
        elevated=True,
    )


def _enrollment_ticket(
    user_id: str,
    *,
    purpose: str,
    challenge: str | None = None,
    challenge_type: str | None = None,
    pending_totp_secret: str | None = None,
) -> MfaTicket:
    return MfaTicket(
        token="enroll-token",
        user_id=user_id,
        purpose=purpose,
        role="INSTITUTION_ADMIN",
        institution_id="22222222-2222-2222-2222-222222222222",
        location_id=None,
        audit_request_id="aud-2",
        challenge=challenge,
        challenge_type=challenge_type,
        pending_totp_secret=pending_totp_secret,
    )


@pytest.fixture
def stub_consume_step_up(authenticated_user):
    with patch.object(
        auth_routes.MfaTicketService,
        "consume_step_up",
        new=AsyncMock(return_value=_step_up_ticket(authenticated_user.id)),
    ) as call:
        yield call


# =============================================================================
# /mfa/factors/webauthn/register/options
# =============================================================================


@pytest.mark.asyncio
async def test_add_passkey_options_consumes_step_up_and_issues_enrollment_ticket(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    stub_consume_step_up,
):
    with patch.object(
        auth_routes.MfaService,
        "generate_webauthn_registration_options",
        new=AsyncMock(return_value=({"challenge": "Y2hh"}, "Y2hh")),
    ), patch.object(
        auth_routes.MfaTicketService,
        "create",
        new=AsyncMock(return_value="enrollment-tok"),
    ) as create_call:
        response = await async_client.post(
            "/api/auth/mfa/factors/webauthn/register/options",
            json={"mfa_ticket": "step-up-elevated"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["enrollment_ticket"] == "enrollment-tok"
    assert body["options"]["challenge"] == "Y2hh"

    # Critical pin: the enrollment ticket is created with the
    # add_factor_webauthn purpose and carries the WebAuthn challenge.
    create_call.assert_awaited_once()
    kwargs = create_call.await_args.kwargs
    assert kwargs["purpose"] == MFA_PURPOSE_ADD_FACTOR_WEBAUTHN
    assert kwargs["extra"]["challenge"] == "Y2hh"
    assert kwargs["extra"]["challenge_type"] == "add_factor_webauthn"


@pytest.mark.asyncio
async def test_add_passkey_options_rejects_invalid_step_up(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
):
    with patch.object(
        auth_routes.MfaTicketService,
        "consume_step_up",
        new=AsyncMock(side_effect=MfaTicketInvalid("Invalid")),
    ), patch.object(
        auth_routes.MfaService,
        "generate_webauthn_registration_options",
        new=AsyncMock(),
    ) as gen_call:
        response = await async_client.post(
            "/api/auth/mfa/factors/webauthn/register/options",
            json={"mfa_ticket": "bogus"},
        )
    assert response.status_code == 401
    # No factor state touched.
    gen_call.assert_not_awaited()


# =============================================================================
# /mfa/factors/webauthn/register/verify
# =============================================================================


@pytest.mark.asyncio
async def test_add_passkey_verify_persists_credential_and_audits(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    audit_log_entries,
):
    enrollment = _enrollment_ticket(
        override_current_user.id,
        purpose=MFA_PURPOSE_ADD_FACTOR_WEBAUTHN,
        challenge="Y2hh",
        challenge_type="add_factor_webauthn",
    )
    created = WebAuthnCredential(
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        user_id=override_current_user.id,
        credential_id="abc",
        public_key="pk",
        device_label="Touch ID",
        aaguid="aaguid",
        credential_device_type="multi_device",
        credential_backed_up=True,
        transports=["internal"],
        created_at=datetime.now(timezone.utc),
        last_used_at=None,
    )

    with patch.object(
        auth_routes.MfaTicketService,
        "consume_enrollment_ticket",
        new=AsyncMock(return_value=enrollment),
    ) as consume_call, patch.object(
        auth_routes.MfaService,
        "verify_webauthn_registration",
        new=AsyncMock(return_value=created),
    ):
        response = await async_client.post(
            "/api/auth/mfa/factors/webauthn/register/verify",
            json={
                "enrollment_ticket": "enroll-tok",
                "credential": {"id": "abc", "response": {}},
                "device_label": "Touch ID",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "registered"
    assert body["credential"]["id"] == created.id
    assert body["credential"]["device_label"] == "Touch ID"

    # Audit row: MFA_ENROLL with add_factor phase.
    consume_call.assert_awaited_once()
    entries = await audit_log_entries()
    matches = [
        e for e in entries
        if e.action == AuditAction.MFA_ENROLL
        and e.outcome == AuditOutcome.SUCCESS
        and e.metadata.get("phase") == "add_factor"
        and e.metadata.get("method") == "webauthn"
    ]
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_add_passkey_verify_rejects_wrong_purpose_ticket(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
):
    """An enrollment ticket with the WRONG purpose (e.g. a step-up
    ticket someone passed by mistake) must not be accepted."""
    with patch.object(
        auth_routes.MfaTicketService,
        "consume_enrollment_ticket",
        new=AsyncMock(side_effect=MfaTicketInvalid("wrong purpose")),
    ), patch.object(
        auth_routes.MfaService,
        "verify_webauthn_registration",
        new=AsyncMock(),
    ) as verify_call:
        response = await async_client.post(
            "/api/auth/mfa/factors/webauthn/register/verify",
            json={"enrollment_ticket": "wrong", "credential": {"id": "x"}},
        )
    assert response.status_code == 401
    verify_call.assert_not_awaited()


# =============================================================================
# /mfa/factors/totp/setup/options
# =============================================================================


@pytest.mark.asyncio
async def test_add_totp_options_when_totp_not_yet_enabled(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    stub_consume_step_up,
):
    no_totp = MfaStatus(webauthn_count=1, totp_enabled=False, recovery_codes_remaining=10)
    with patch.object(
        auth_routes.MfaService, "status_for_user", new=AsyncMock(return_value=no_totp),
    ), patch.object(
        auth_routes.MfaService, "new_totp_secret", new=lambda: "BASE32SECRET234",
    ), patch.object(
        auth_routes.MfaService, "totp_uri", new=lambda *, secret, email: f"otpauth://totp/x:{email}?secret={secret}",
    ), patch.object(
        auth_routes.MfaTicketService,
        "create",
        new=AsyncMock(return_value="enrollment-totp"),
    ) as create_call:
        response = await async_client.post(
            "/api/auth/mfa/factors/totp/setup/options",
            json={"mfa_ticket": "step-up-elevated"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["enrollment_ticket"] == "enrollment-totp"
    assert body["secret"] == "BASE32SECRET234"
    create_call.assert_awaited_once()
    kwargs = create_call.await_args.kwargs
    assert kwargs["purpose"] == MFA_PURPOSE_ADD_FACTOR_TOTP
    assert kwargs["extra"]["pending_totp_secret"] == "BASE32SECRET234"


@pytest.mark.asyncio
async def test_add_totp_options_rejects_when_totp_already_enabled(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    stub_consume_step_up,
):
    already = MfaStatus(webauthn_count=1, totp_enabled=True, recovery_codes_remaining=10)
    with patch.object(
        auth_routes.MfaService, "status_for_user", new=AsyncMock(return_value=already),
    ), patch.object(
        auth_routes.MfaTicketService, "create", new=AsyncMock(),
    ) as create_call:
        response = await async_client.post(
            "/api/auth/mfa/factors/totp/setup/options",
            json={"mfa_ticket": "step-up-elevated"},
        )
    assert response.status_code == 400
    assert "already enabled" in response.json()["detail"]
    create_call.assert_not_awaited()


# =============================================================================
# /mfa/factors/totp/setup/verify
# =============================================================================


@pytest.mark.asyncio
async def test_add_totp_verify_persists_factor_and_audits(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    audit_log_entries,
):
    enrollment = _enrollment_ticket(
        override_current_user.id,
        purpose=MFA_PURPOSE_ADD_FACTOR_TOTP,
        pending_totp_secret="BASE32SECRET234",
        challenge_type="add_factor_totp",
    )
    no_totp_yet = MfaStatus(webauthn_count=1, totp_enabled=False, recovery_codes_remaining=10)
    with patch.object(
        auth_routes.MfaTicketService,
        "consume_enrollment_ticket",
        new=AsyncMock(return_value=enrollment),
    ), patch.object(
        auth_routes.MfaService,
        "status_for_user",
        new=AsyncMock(return_value=no_totp_yet),
    ), patch.object(
        auth_routes.MfaService,
        "verify_and_store_totp_setup",
        new=AsyncMock(return_value=UserTotpFactor(user_id=override_current_user.id)),
    ):
        response = await async_client.post(
            "/api/auth/mfa/factors/totp/setup/verify",
            json={"enrollment_ticket": "enroll-totp", "code": "123456"},
        )
    assert response.status_code == 200
    assert response.json() == {"status": "enrolled", "totp_enabled": True}

    entries = await audit_log_entries()
    matches = [
        e for e in entries
        if e.action == AuditAction.MFA_ENROLL
        and e.metadata.get("phase") == "add_factor"
        and e.metadata.get("method") == "totp"
    ]
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_add_totp_verify_rejects_when_totp_enabled_between_options_and_verify(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
):
    """The /options endpoint already rejects an enrolled user, but a
    concurrent login-flow enrollment could land between /options and
    /verify. The verify endpoint must re-check and refuse, because
    ``verify_and_store_totp_setup`` will otherwise quietly overwrite
    the row that was just created."""
    enrollment = _enrollment_ticket(
        override_current_user.id,
        purpose=MFA_PURPOSE_ADD_FACTOR_TOTP,
        pending_totp_secret="BASE32SECRET234",
        challenge_type="add_factor_totp",
    )
    already_enabled = MfaStatus(webauthn_count=0, totp_enabled=True, recovery_codes_remaining=0)
    with patch.object(
        auth_routes.MfaTicketService,
        "consume_enrollment_ticket",
        new=AsyncMock(return_value=enrollment),
    ), patch.object(
        auth_routes.MfaService,
        "status_for_user",
        new=AsyncMock(return_value=already_enabled),
    ), patch.object(
        auth_routes.MfaService,
        "verify_and_store_totp_setup",
        new=AsyncMock(),
    ) as verify_call:
        response = await async_client.post(
            "/api/auth/mfa/factors/totp/setup/verify",
            json={"enrollment_ticket": "enroll-totp", "code": "123456"},
        )
    assert response.status_code == 400
    assert "already enabled" in response.json()["detail"]
    verify_call.assert_not_awaited()  # never touched the factor row
