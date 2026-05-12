"""Step-up MFA endpoints.

The step-up flow is the gate between an already-authenticated session
and a sensitive factor-management operation. These tests cover the
endpoints that issue and consume the elevated ticket:

  - POST /auth/mfa/step-up/challenge
  - POST /auth/mfa/step-up/totp/verify
  - POST /auth/mfa/step-up/webauthn/authenticate/options
  - POST /auth/mfa/step-up/webauthn/authenticate/verify
  - POST /auth/mfa/step-up/recovery-code/verify

And the cross-cutting safety properties:

  - Login-purpose tickets cannot be redeemed for a session via the
    step-up verify endpoints, and step-up tickets cannot be redeemed
    for a session via the login verify endpoints.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import get_current_active_user
from src.app.api.routes import auth as auth_routes
from src.app.main import app
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.mfa import (
    MFA_PURPOSE_LOGIN,
    MFA_PURPOSE_STEP_UP,
    MfaStatus,
    MfaTicket,
    MfaTicketInvalid,
    MfaVerificationFailed,
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
    async def _noop_session(*_args, **_kwargs):
        yield AsyncMock()

    with patch.object(auth_routes, "_auth_db_session", _noop_session):
        yield


def _ticket(
    user_id: str,
    *,
    purpose: str = MFA_PURPOSE_STEP_UP,
    challenge: str | None = None,
    challenge_type: str | None = None,
) -> MfaTicket:
    return MfaTicket(
        token="step-up-token",
        user_id=user_id,
        purpose=purpose,
        role="INSTITUTION_ADMIN",
        institution_id="22222222-2222-2222-2222-222222222222",
        location_id=None,
        audit_request_id="audit-req-1",
        challenge=challenge,
        challenge_type=challenge_type,
    )


# =============================================================================
# POST /mfa/step-up/challenge — opens the flow, requires enrolled factor
# =============================================================================


@pytest.mark.asyncio
async def test_step_up_challenge_issues_ticket_for_enrolled_user(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    enrolled_status = MfaStatus(webauthn_count=1, totp_enabled=True, recovery_codes_remaining=10)
    with patch.object(
        auth_routes.MfaService,
        "status_for_user",
        new=AsyncMock(return_value=enrolled_status),
    ), patch.object(
        auth_routes.MfaTicketService,
        "create",
        new=AsyncMock(return_value="opaque-token"),
    ) as create_call:
        response = await async_client.post("/api/auth/mfa/step-up/challenge")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "step_up_required"
    assert body["mfa_ticket"] == "opaque-token"
    # methods reflect what the user actually has, available_methods_for_role
    # filters TOTP for super admin (not this user). Both auth factors plus
    # recovery codes are surfaced.
    assert set(body["methods"]) == {"webauthn", "totp", "recovery_code"}
    assert body["email"] == "admin@example.com"

    # Ticket creation is pinned to MFA_PURPOSE_STEP_UP — the boundary
    # that stops a step-up ticket from being usable to start a session.
    kwargs = create_call.await_args.kwargs
    assert kwargs["purpose"] == MFA_PURPOSE_STEP_UP
    assert kwargs["user"].id == override_current_user.id


@pytest.mark.asyncio
async def test_step_up_challenge_rejects_user_without_any_factor(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    empty_status = MfaStatus(webauthn_count=0, totp_enabled=False, recovery_codes_remaining=0)
    with patch.object(
        auth_routes.MfaService,
        "status_for_user",
        new=AsyncMock(return_value=empty_status),
    ), patch.object(
        auth_routes.MfaTicketService, "create", new=AsyncMock(),
    ) as create_call:
        response = await async_client.post("/api/auth/mfa/step-up/challenge")
    assert response.status_code == 400
    assert "No MFA factor" in response.json()["detail"]
    create_call.assert_not_awaited()


# =============================================================================
# POST /mfa/step-up/totp/verify
# =============================================================================


@pytest.mark.asyncio
async def test_step_up_totp_verify_elevates_ticket(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    audit_log_entries,
):
    ticket = _ticket(override_current_user.id)
    elevated = MfaTicket(**{**ticket.__dict__, "elevated": True})
    with patch.object(
        auth_routes,
        "_ticket_from_request",
        new=AsyncMock(return_value=ticket),
    ), patch.object(
        auth_routes.MfaService, "verify_totp", new=AsyncMock(return_value=None),
    ) as verify_call, patch.object(
        auth_routes.MfaTicketService,
        "mark_step_up_elevated",
        new=AsyncMock(return_value=elevated),
    ) as mark_call:
        response = await async_client.post(
            "/api/auth/mfa/step-up/totp/verify",
            json={"mfa_ticket": "step-up-token", "code": "123456"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "step_up_complete"
    assert body["mfa_ticket"] == "step-up-token"
    verify_call.assert_awaited_once()
    mark_call.assert_awaited_once()

    # The audit row records the verify event scoped to the step-up phase.
    entries = await audit_log_entries()
    matches = [
        e for e in entries
        if e.action == AuditAction.MFA_VERIFY
        and e.outcome == AuditOutcome.SUCCESS
        and e.metadata.get("phase") == "step_up_verify"
    ]
    assert len(matches) == 1
    assert matches[0].metadata["method"] == "totp"


@pytest.mark.asyncio
async def test_step_up_totp_verify_audits_failure_and_returns_401(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    audit_log_entries,
):
    """Failed verification must NOT elevate the ticket — and must
    leave a §164.312(b) FAILURE audit row."""
    ticket = _ticket(override_current_user.id)
    with patch.object(
        auth_routes,
        "_ticket_from_request",
        new=AsyncMock(return_value=ticket),
    ), patch.object(
        auth_routes.MfaService,
        "verify_totp",
        new=AsyncMock(side_effect=MfaVerificationFailed("Invalid TOTP code")),
    ), patch.object(
        auth_routes.MfaTicketService, "mark_step_up_elevated", new=AsyncMock(),
    ) as mark_call:
        response = await async_client.post(
            "/api/auth/mfa/step-up/totp/verify",
            json={"mfa_ticket": "step-up-token", "code": "000000"},
        )

    assert response.status_code == 401
    mark_call.assert_not_awaited()  # never elevate on failure
    entries = await audit_log_entries()
    # `_audit_mfa_failure` records the FAILURE outcome — exact enum
    # value matches whatever the login-side failure path uses (matches
    # the row that the audit decorator writes for every failed verify
    # attempt). We assert the phase tag is the discriminator so step-up
    # vs login failures can be told apart in forensic review.
    failures = [
        e for e in entries
        if e.action == AuditAction.MFA_VERIFY
        and "FAILURE" in e.outcome.value
        and e.metadata.get("phase") == "step_up_verify"
    ]
    assert len(failures) == 1


# =============================================================================
# POST /mfa/step-up/webauthn/authenticate/options + verify
# =============================================================================


@pytest.mark.asyncio
async def test_step_up_webauthn_options_stores_challenge_on_ticket(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    ticket = _ticket(override_current_user.id)
    with patch.object(
        auth_routes,
        "_ticket_from_request",
        new=AsyncMock(return_value=ticket),
    ), patch.object(
        auth_routes.MfaService,
        "generate_webauthn_authentication_options",
        new=AsyncMock(return_value=({"challenge": "Y2hhbGxlbmdl"}, "Y2hhbGxlbmdl")),
    ), patch.object(
        auth_routes.MfaTicketService, "update", new=AsyncMock(return_value=ticket),
    ) as update_call:
        response = await async_client.post(
            "/api/auth/mfa/step-up/webauthn/authenticate/options",
            json={"mfa_ticket": "step-up-token"},
        )

    assert response.status_code == 200
    assert response.json()["options"]["challenge"] == "Y2hhbGxlbmdl"
    # Challenge must be persisted on the ticket so the verify call can
    # require expected_challenge match.
    update_call.assert_awaited_once()
    kwargs = update_call.await_args.kwargs
    assert kwargs["challenge"] == "Y2hhbGxlbmdl"
    assert kwargs["challenge_type"] == "step_up_webauthn"


@pytest.mark.asyncio
async def test_step_up_webauthn_options_rejects_cross_user_ticket(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    """A step-up ticket bound to a different user_id (e.g. resurrected
    from another session) must not be usable in this account's flow."""
    other_user_ticket = _ticket("00000000-0000-0000-0000-000000000000")
    with patch.object(
        auth_routes,
        "_ticket_from_request",
        new=AsyncMock(return_value=other_user_ticket),
    ), patch.object(
        auth_routes.MfaService,
        "generate_webauthn_authentication_options",
        new=AsyncMock(),
    ) as options_call:
        response = await async_client.post(
            "/api/auth/mfa/step-up/webauthn/authenticate/options",
            json={"mfa_ticket": "step-up-token"},
        )
    assert response.status_code == 400
    assert "current user" in response.json()["detail"]
    options_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_step_up_webauthn_verify_rejects_when_challenge_missing(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    """The options endpoint must have run first — without it the ticket
    has no challenge stored and verify must fail closed."""
    ticket = _ticket(override_current_user.id)  # no challenge / type
    with patch.object(
        auth_routes, "_ticket_from_request", new=AsyncMock(return_value=ticket),
    ):
        response = await async_client.post(
            "/api/auth/mfa/step-up/webauthn/authenticate/verify",
            json={"mfa_ticket": "step-up-token", "credential": {}},
        )
    assert response.status_code == 400
    assert "challenge is missing" in response.json()["detail"]


@pytest.mark.asyncio
async def test_step_up_webauthn_verify_elevates_on_success(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    audit_log_entries,
):
    ticket = _ticket(
        override_current_user.id,
        challenge="Y2hhbGxlbmdl",
        challenge_type="step_up_webauthn",
    )
    elevated = MfaTicket(**{**ticket.__dict__, "elevated": True})
    with patch.object(
        auth_routes, "_ticket_from_request", new=AsyncMock(return_value=ticket),
    ), patch.object(
        auth_routes.MfaService,
        "verify_webauthn_authentication",
        new=AsyncMock(return_value=None),
    ), patch.object(
        auth_routes.MfaTicketService,
        "mark_step_up_elevated",
        new=AsyncMock(return_value=elevated),
    ):
        response = await async_client.post(
            "/api/auth/mfa/step-up/webauthn/authenticate/verify",
            json={"mfa_ticket": "step-up-token", "credential": {"id": "cred-id"}},
        )

    assert response.status_code == 200
    entries = await audit_log_entries()
    success = [
        e for e in entries
        if e.action == AuditAction.MFA_VERIFY
        and e.outcome == AuditOutcome.SUCCESS
        and e.metadata.get("method") == "webauthn"
    ]
    assert len(success) == 1
    assert success[0].metadata["phase"] == "step_up_verify"


# =============================================================================
# POST /mfa/step-up/recovery-code/verify
# =============================================================================


@pytest.mark.asyncio
async def test_step_up_recovery_code_verify_elevates_ticket(
    async_client: AsyncClient,
    override_current_user: User,
    stub_db_session,
    audit_log_entries,
):
    ticket = _ticket(override_current_user.id)
    elevated = MfaTicket(**{**ticket.__dict__, "elevated": True})
    with patch.object(
        auth_routes, "_ticket_from_request", new=AsyncMock(return_value=ticket),
    ), patch.object(
        auth_routes.MfaService, "use_recovery_code", new=AsyncMock(return_value=None),
    ), patch.object(
        auth_routes.MfaTicketService,
        "mark_step_up_elevated",
        new=AsyncMock(return_value=elevated),
    ):
        response = await async_client.post(
            "/api/auth/mfa/step-up/recovery-code/verify",
            json={"mfa_ticket": "step-up-token", "code": "abcd-efgh"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "step_up_complete"


# =============================================================================
# Cross-flow boundary: step-up tickets cannot start a session.
# =============================================================================


@pytest.mark.asyncio
async def test_login_totp_verify_rejects_step_up_ticket(
    async_client: AsyncClient, stub_db_session
):
    """`_complete_mfa_auth` rejects step-up tickets even if they passed
    the verify primitive. The login path must remain login-only."""
    step_up = _ticket("11111111-1111-1111-1111-111111111111", purpose=MFA_PURPOSE_STEP_UP)
    with patch.object(
        auth_routes,
        "_ticket_from_request",
        new=AsyncMock(return_value=step_up),
    ), patch.object(
        auth_routes,
        "_user_for_mfa_ticket",
        new=AsyncMock(return_value=User(
            id=step_up.user_id,
            email="x@y.test",
            role=UserRole.INSTITUTION_ADMIN.value,
            institution_id=step_up.institution_id,
            is_active=True,
        )),
    ), patch.object(
        auth_routes.MfaService, "verify_totp", new=AsyncMock(return_value=None),
    ):
        response = await async_client.post(
            "/api/auth/mfa/totp/verify",
            json={"mfa_ticket": "step-up-token", "code": "123456"},
        )

    assert response.status_code == 400
    assert "step-up" in response.json()["detail"].lower() or "session" in response.json()["detail"].lower()


# =============================================================================
# Authorization-before-mutation guard.
#
# When a step-up ticket is presented that's bound to a different user,
# the endpoint must refuse BEFORE touching factor state. Without this
# guard, an attacker who shares the victim's IP+UA fingerprint can grief
# the victim's MFA factors:
#
#   - TOTP: each verify call consumes a timestep (last_accepted_time_step).
#   - Recovery code: each verify call marks a code used_at.
#   - WebAuthn: each verify call bumps sign_count + last_used_at.
#
# These tests pin that the mismatch is caught before MfaService.verify_*
# is even called.
# =============================================================================


@pytest.mark.asyncio
async def test_step_up_totp_verify_rejects_cross_user_ticket_without_verifying(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    other_user_ticket = _ticket("99999999-9999-9999-9999-999999999999")
    with patch.object(
        auth_routes, "_ticket_from_request", new=AsyncMock(return_value=other_user_ticket),
    ), patch.object(
        auth_routes.MfaService, "verify_totp", new=AsyncMock(),
    ) as verify_call, patch.object(
        auth_routes.MfaTicketService, "mark_step_up_elevated", new=AsyncMock(),
    ) as mark_call:
        response = await async_client.post(
            "/api/auth/mfa/step-up/totp/verify",
            json={"mfa_ticket": "stolen-token", "code": "123456"},
        )

    assert response.status_code == 400
    # Critical: no factor state was touched.
    verify_call.assert_not_awaited()
    mark_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_step_up_webauthn_verify_rejects_cross_user_ticket_without_verifying(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    other_user_ticket = _ticket(
        "99999999-9999-9999-9999-999999999999",
        challenge="ch",
        challenge_type="step_up_webauthn",
    )
    with patch.object(
        auth_routes, "_ticket_from_request", new=AsyncMock(return_value=other_user_ticket),
    ), patch.object(
        auth_routes.MfaService, "verify_webauthn_authentication", new=AsyncMock(),
    ) as verify_call, patch.object(
        auth_routes.MfaTicketService, "mark_step_up_elevated", new=AsyncMock(),
    ) as mark_call:
        response = await async_client.post(
            "/api/auth/mfa/step-up/webauthn/authenticate/verify",
            json={"mfa_ticket": "stolen-token", "credential": {"id": "x"}},
        )
    assert response.status_code == 400
    verify_call.assert_not_awaited()
    mark_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_step_up_recovery_code_verify_rejects_cross_user_ticket_without_consuming(
    async_client: AsyncClient, override_current_user: User, stub_db_session
):
    other_user_ticket = _ticket("99999999-9999-9999-9999-999999999999")
    with patch.object(
        auth_routes, "_ticket_from_request", new=AsyncMock(return_value=other_user_ticket),
    ), patch.object(
        auth_routes.MfaService, "use_recovery_code", new=AsyncMock(),
    ) as use_call, patch.object(
        auth_routes.MfaTicketService, "mark_step_up_elevated", new=AsyncMock(),
    ) as mark_call:
        response = await async_client.post(
            "/api/auth/mfa/step-up/recovery-code/verify",
            json={"mfa_ticket": "stolen-token", "code": "abcd-efgh"},
        )
    assert response.status_code == 400
    use_call.assert_not_awaited()
    mark_call.assert_not_awaited()
