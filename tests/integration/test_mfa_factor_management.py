"""Factor management endpoints — list / remove passkey, disable TOTP.

These let a user swap a lost or stolen authenticator without admin
intervention. Each state-changing endpoint writes a §164.312(b)
audit row; the listing endpoint never exposes the public key.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import get_current_active_user
from src.app.api.routes import auth as auth_routes
from src.app.main import app
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.mfa import UserTotpFactor, WebAuthnCredential
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.password_service import PasswordService


@pytest.fixture
def authenticated_user() -> User:
    """An MFA-authenticated user. Wired via dependency override below."""
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
    """Bypass JWT/MFA gate by injecting the test user directly."""
    app.dependency_overrides[get_current_active_user] = lambda: authenticated_user
    try:
        yield authenticated_user
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)


@pytest.fixture
def stub_db_session():
    """Stub get_db_session() so routes that open a session don't need a DB."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_session():
        yield AsyncMock()

    with patch.object(auth_routes, "get_db_session", _noop_session):
        yield


def _fake_step_up_ticket(user_id: str):
    """Mock-shaped MfaTicket compatible with the audit-row builders."""
    from src.app.services.mfa import MFA_PURPOSE_STEP_UP, MfaTicket

    return MfaTicket(
        token="step-up-token",
        user_id=user_id,
        purpose=MFA_PURPOSE_STEP_UP,
        role="INSTITUTION_ADMIN",
        institution_id="22222222-2222-2222-2222-222222222222",
        location_id=None,
        audit_request_id="step-up-audit-id",
        elevated=True,
    )


@pytest.fixture
def stub_step_up(authenticated_user):
    """Pretend the step-up ticket validation always succeeds — frees the
    factor-management tests from spinning up Redis. Tests that need to
    exercise the rejection paths bypass this fixture."""
    fake_ticket = _fake_step_up_ticket(authenticated_user.id)
    with patch.object(
        auth_routes.MfaTicketService,
        "consume_step_up",
        new=AsyncMock(return_value=fake_ticket),
    ) as consumed:
        yield consumed


# Body required by the step-up-gated endpoints. Constant per test —
# the contents are irrelevant because we stub the consume_step_up
# validation.
_STEP_UP_BODY = {"mfa_ticket": "step-up-token"}


# =============================================================================
# GET /mfa/webauthn — list credentials, no public key in response
# =============================================================================

@pytest.mark.asyncio
async def test_webauthn_list_returns_metadata_only(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    cred = WebAuthnCredential(
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        user_id=override_current_user.id,
        credential_id="abcdef",
        public_key="THIS_PUBLIC_KEY_MUST_NEVER_LEAK_TO_THE_API_RESPONSE",
        sign_count=3,
        device_label="MacBook Touch ID",
        aaguid="aaguid-1",
        credential_device_type="multi_device",
        credential_backed_up=True,
        transports=["internal"],
        created_at=datetime.now(timezone.utc),
        last_used_at=None,
    )
    with patch.object(
        auth_routes.MfaService,
        "webauthn_credentials",
        new=AsyncMock(return_value=[cred]),
    ):
        response = await async_client.get("/api/auth/mfa/webauthn")

    assert response.status_code == 200
    body = response.json()
    assert len(body["credentials"]) == 1
    item = body["credentials"][0]
    assert item["id"] == cred.id
    assert item["device_label"] == "MacBook Touch ID"
    assert item["credential_backed_up"] is True
    assert item["transports"] == ["internal"]

    # Critical contract: public key never travels over the wire.
    assert "public_key" not in item
    assert "THIS_PUBLIC_KEY" not in response.text
    assert "credential_id" not in item, (
        "raw credential_id is also a fingerprint — keep the row PK as the public id"
    )


# =============================================================================
# DELETE /mfa/webauthn/{id} — remove a passkey, audit, 404 on miss
# =============================================================================

@pytest.mark.asyncio
async def test_webauthn_remove_writes_audit_row(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    stub_step_up,
    audit_log_entries,
):
    removed = WebAuthnCredential(
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        user_id=override_current_user.id,
        credential_id="abcdef",
        public_key="key",
        device_label="Old phone",
    )
    with patch.object(
        auth_routes.MfaService,
        "remove_webauthn_credential",
        new=AsyncMock(return_value=removed),
    ) as remove_call:
        response = await async_client.request(
            "DELETE",
            f"/api/auth/mfa/webauthn/{removed.id}",
            json=_STEP_UP_BODY,
        )

    assert response.status_code == 200
    assert response.json() == {"message": "Passkey removed"}
    remove_call.assert_awaited_once()
    kwargs = remove_call.await_args.kwargs
    assert kwargs == {
        "user_id": override_current_user.id,
        "credential_pk": removed.id,
    }

    entries = await audit_log_entries()
    matches = [
        e for e in entries
        if e.action == AuditAction.MFA_FACTOR_REMOVE
        and e.outcome == AuditOutcome.SUCCESS
    ]
    assert len(matches) == 1
    row = matches[0]
    assert row.target_resource == f"user:{override_current_user.id}/webauthn:{removed.id}"
    assert row.metadata["method"] == "webauthn"
    assert row.metadata["device_label"] == "Old phone"


@pytest.mark.asyncio
async def test_webauthn_remove_returns_404_for_missing_credential(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    stub_step_up,
    audit_log_entries,
):
    with patch.object(
        auth_routes.MfaService,
        "remove_webauthn_credential",
        new=AsyncMock(return_value=None),
    ):
        response = await async_client.request(
            "DELETE",
            "/api/auth/mfa/webauthn/00000000-0000-0000-0000-000000000000",
            json=_STEP_UP_BODY,
        )

    assert response.status_code == 404
    entries = await audit_log_entries()
    # 404 on a non-existent credential should NOT log a SUCCESS row.
    matches = [
        e for e in entries
        if e.action == AuditAction.MFA_FACTOR_REMOVE
        and e.outcome == AuditOutcome.SUCCESS
    ]
    assert matches == []


# =============================================================================
# POST /mfa/totp/disable — idempotent, audits state-change only
# =============================================================================

@pytest.mark.asyncio
async def test_totp_disable_audits_state_change(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    stub_step_up,
    audit_log_entries,
):
    with patch.object(
        auth_routes.MfaService,
        "disable_totp",
        new=AsyncMock(return_value=True),
    ):
        response = await async_client.post(
            "/api/auth/mfa/totp/disable", json=_STEP_UP_BODY,
        )

    assert response.status_code == 200
    assert response.json() == {"message": "Authenticator app disabled"}
    entries = await audit_log_entries()
    matches = [
        e for e in entries
        if e.action == AuditAction.MFA_FACTOR_DISABLE
        and e.outcome == AuditOutcome.SUCCESS
    ]
    assert len(matches) == 1
    assert matches[0].target_resource == f"user:{override_current_user.id}/totp"


@pytest.mark.asyncio
async def test_totp_disable_idempotent_does_not_audit_when_already_off(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    stub_step_up,
    audit_log_entries,
):
    """Disabling TOTP when no factor exists must NOT generate audit noise."""
    with patch.object(
        auth_routes.MfaService,
        "disable_totp",
        new=AsyncMock(return_value=False),
    ):
        response = await async_client.post(
            "/api/auth/mfa/totp/disable", json=_STEP_UP_BODY,
        )

    assert response.status_code == 200
    assert response.json() == {"message": "Authenticator app was not enabled"}
    entries = await audit_log_entries()
    assert [
        e for e in entries
        if e.action == AuditAction.MFA_FACTOR_DISABLE
    ] == []


# =============================================================================
# Service-level unit tests (no FastAPI app)
# =============================================================================

class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, *, scalar_value=None):
        self.scalar_value = scalar_value
        self.deleted = []
        self.flushed = False

    async def scalar(self, _query):
        return self.scalar_value

    async def delete(self, row):
        self.deleted.append(row)

    async def flush(self):
        self.flushed = True


@pytest.mark.asyncio
async def test_service_remove_webauthn_returns_none_when_missing():
    from src.app.services.mfa import MfaService

    session = _FakeSession(scalar_value=None)
    result = await MfaService(session).remove_webauthn_credential(  # type: ignore[arg-type]
        user_id="user-1", credential_pk="missing"
    )
    assert result is None
    assert session.deleted == []


@pytest.mark.asyncio
async def test_service_remove_webauthn_deletes_row():
    from src.app.services.mfa import MfaService

    cred = WebAuthnCredential(
        id="cred-pk", user_id="user-1", credential_id="x", public_key="y"
    )
    session = _FakeSession(scalar_value=cred)
    result = await MfaService(session).remove_webauthn_credential(  # type: ignore[arg-type]
        user_id="user-1", credential_pk="cred-pk"
    )
    assert result is cred
    assert session.deleted == [cred]
    assert session.flushed is True


@pytest.mark.asyncio
async def test_service_disable_totp_returns_false_when_no_factor():
    from src.app.services.mfa import MfaService

    session = _FakeSession(scalar_value=None)
    result = await MfaService(session).disable_totp(user_id="user-1")  # type: ignore[arg-type]
    assert result is False
    assert session.deleted == []


@pytest.mark.asyncio
async def test_service_disable_totp_deletes_row():
    from src.app.services.mfa import MfaService

    factor = UserTotpFactor(user_id="user-1", secret_encrypted="enc")
    session = _FakeSession(scalar_value=factor)
    result = await MfaService(session).disable_totp(user_id="user-1")  # type: ignore[arg-type]
    assert result is True
    assert session.deleted == [factor]
    assert session.flushed is True


# =============================================================================
# Step-up enforcement — every destructive endpoint refuses to proceed
# without a valid elevated ticket.
# =============================================================================

from src.app.services.mfa import MfaTicketInvalid


@pytest.mark.asyncio
async def test_recovery_codes_regenerate_rejects_missing_ticket(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    """No body means FastAPI rejects at validation — 422, no audit row,
    no service call. Verifies the route's contract requires the ticket."""
    with patch.object(
        auth_routes.MfaService, "replace_recovery_codes", new=AsyncMock(),
    ) as service_call:
        response = await async_client.post(
            "/api/auth/mfa/recovery-codes/regenerate"
        )
    assert response.status_code == 422
    service_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_codes_regenerate_rejects_invalid_ticket(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    """Body present but step-up consume raises MfaTicketInvalid — 401."""
    with patch.object(
        auth_routes.MfaTicketService,
        "consume_step_up",
        new=AsyncMock(side_effect=MfaTicketInvalid("Invalid")),
    ), patch.object(
        auth_routes.MfaService, "replace_recovery_codes", new=AsyncMock(),
    ) as service_call:
        response = await async_client.post(
            "/api/auth/mfa/recovery-codes/regenerate",
            json={"mfa_ticket": "bogus"},
        )
    assert response.status_code == 401
    service_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_webauthn_remove_rejects_invalid_step_up(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    with patch.object(
        auth_routes.MfaTicketService,
        "consume_step_up",
        new=AsyncMock(side_effect=MfaTicketInvalid("Invalid")),
    ), patch.object(
        auth_routes.MfaService, "remove_webauthn_credential", new=AsyncMock(),
    ) as service_call:
        response = await async_client.request(
            "DELETE",
            "/api/auth/mfa/webauthn/00000000-0000-0000-0000-000000000000",
            json={"mfa_ticket": "bogus"},
        )
    assert response.status_code == 401
    service_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_totp_disable_rejects_invalid_step_up(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    with patch.object(
        auth_routes.MfaTicketService,
        "consume_step_up",
        new=AsyncMock(side_effect=MfaTicketInvalid("Invalid")),
    ), patch.object(
        auth_routes.MfaService, "disable_totp", new=AsyncMock(),
    ) as service_call:
        response = await async_client.post(
            "/api/auth/mfa/totp/disable",
            json={"mfa_ticket": "bogus"},
        )
    assert response.status_code == 401
    service_call.assert_not_awaited()
