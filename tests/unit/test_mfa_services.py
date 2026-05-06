from __future__ import annotations

from types import SimpleNamespace

import pyotp
import pytest
from webauthn.helpers import bytes_to_base64url

from src.app.models.mfa import MfaRecoveryCode, UserTotpFactor, WebAuthnCredential
from src.app.models.user import User, UserRole
from src.app.services.mfa import (
    MfaService,
    MfaStatus,
    MfaTicketInvalid,
    MfaTicketService,
    MfaVerificationFailed,
)
from src.app.services.password_service import PasswordService
from src.app.services.refresh_token_service import RefreshTokenService


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def setex(self, key: str, _ttl: int, value: str) -> bool:
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.values.pop(key, None) is not None else 0


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, *, scalar_value=None, rows=None) -> None:
        self.scalar_value = scalar_value
        self.rows = rows or []
        self.flushed = False

    async def scalar(self, _query):
        return self.scalar_value

    async def execute(self, _query):
        return _ScalarResult(self.rows)

    async def flush(self) -> None:
        self.flushed = True


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    redis = _FakeRedis()

    async def _get_client(_cls):
        return redis

    monkeypatch.setattr(RefreshTokenService, "get_client", classmethod(_get_client))
    return redis


@pytest.mark.asyncio
async def test_mfa_ticket_is_bound_to_purpose_ip_and_user_agent(fake_redis: _FakeRedis):
    user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="user@example.com",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="22222222-2222-2222-2222-222222222222",
    )

    token = await MfaTicketService.create(
        user=user,
        purpose="login",
        client_ip="203.0.113.10",
        user_agent="test-agent",
        audit_request_id="audit-1",
    )

    ticket = await MfaTicketService.get(
        token,
        client_ip="203.0.113.10",
        user_agent="test-agent",
        purpose="login",
    )
    assert ticket.user_id == user.id

    with pytest.raises(MfaTicketInvalid):
        await MfaTicketService.get(
            token,
            client_ip="203.0.113.10",
            user_agent="test-agent",
            purpose="reset_password",
        )

    with pytest.raises(MfaTicketInvalid):
        await MfaTicketService.get(
            token,
            client_ip="203.0.113.99",
            user_agent="test-agent",
            purpose="login",
        )

    await MfaTicketService.consume(ticket)
    assert fake_redis.values == {}


def test_super_admin_requires_passkey_even_when_totp_exists() -> None:
    status = MfaStatus(webauthn_count=0, totp_enabled=True, recovery_codes_remaining=2)

    assert status.enrolled_for_role(UserRole.SUPER_ADMIN.value) is False
    assert status.available_methods_for_role(UserRole.SUPER_ADMIN.value) == ["recovery_code"]
    assert status.setup_methods_for_role(UserRole.SUPER_ADMIN.value) == ["webauthn"]


@pytest.mark.asyncio
async def test_totp_accepts_one_code_once_only() -> None:
    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()
    factor = UserTotpFactor(user_id="11111111-1111-1111-1111-111111111111")
    factor.secret = secret
    session = _FakeSession(scalar_value=factor)
    service = MfaService(session)  # type: ignore[arg-type]

    await service.verify_totp(user_id=factor.user_id, code=code)

    assert factor.last_accepted_time_step is not None
    assert session.flushed is True
    with pytest.raises(MfaVerificationFailed):
        await service.verify_totp(user_id=factor.user_id, code=code)


@pytest.mark.asyncio
async def test_recovery_code_is_hashed_and_single_use() -> None:
    code = "ABCDE-FG234-HJKLM"
    row = MfaRecoveryCode(
        user_id="11111111-1111-1111-1111-111111111111",
        code_hash=PasswordService.hash_secret(MfaService.normalize_recovery_code(code)),
    )
    session = _FakeSession(rows=[row])
    service = MfaService(session)  # type: ignore[arg-type]

    used = await service.use_recovery_code(user_id=row.user_id, code=code.lower())

    assert used is row
    assert row.used_at is not None
    assert session.flushed is True


@pytest.mark.asyncio
async def test_webauthn_registration_rejects_duplicate_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    credential_id = bytes_to_base64url(b"credential-id")
    existing = WebAuthnCredential(
        user_id="11111111-1111-1111-1111-111111111111",
        credential_id=credential_id,
        public_key=bytes_to_base64url(b"public-key"),
    )
    session = _FakeSession(scalar_value=existing)
    service = MfaService(session)  # type: ignore[arg-type]
    user = User(id=existing.user_id, email="user@example.com", role=UserRole.INSTITUTION_ADMIN.value)

    monkeypatch.setattr(
        "src.app.services.mfa.verify_registration_response",
        lambda **_kwargs: SimpleNamespace(
            credential_id=b"credential-id",
            credential_public_key=b"new-public-key",
            sign_count=1,
            aaguid="aaguid",
            credential_device_type=SimpleNamespace(value="single_device"),
            credential_backed_up=False,
        ),
    )

    with pytest.raises(MfaVerificationFailed):
        await service.verify_webauthn_registration(
            user=user,
            credential={"id": credential_id, "response": {}},
            expected_challenge=bytes_to_base64url(b"challenge"),
            device_label="Laptop",
        )


@pytest.mark.asyncio
async def test_webauthn_authentication_rejects_sign_counter_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_id = bytes_to_base64url(b"credential-id")
    stored = WebAuthnCredential(
        user_id="11111111-1111-1111-1111-111111111111",
        credential_id=credential_id,
        public_key=bytes_to_base64url(b"public-key"),
        sign_count=5,
    )
    session = _FakeSession(scalar_value=stored)
    service = MfaService(session)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "src.app.services.mfa.verify_authentication_response",
        lambda **_kwargs: SimpleNamespace(new_sign_count=4),
    )

    with pytest.raises(MfaVerificationFailed):
        await service.verify_webauthn_authentication(
            user_id=stored.user_id,
            credential={"id": credential_id},
            expected_challenge=bytes_to_base64url(b"challenge"),
        )
