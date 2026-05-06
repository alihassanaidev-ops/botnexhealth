"""MFA services for Redis tickets, WebAuthn/passkeys, TOTP, and recovery codes."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pyotp
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from src.app.config import settings
from src.app.models.mfa import MfaRecoveryCode, UserTotpFactor, WebAuthnCredential
from src.app.models.user import User, UserRole
from src.app.security import keyed_hash
from src.app.services.password_service import PasswordService
from src.app.services.refresh_token_service import RefreshTokenService


class MfaError(RuntimeError):
    """Base MFA error."""


class MfaStoreUnavailable(MfaError):
    """Redis-backed MFA state is unavailable."""


class MfaTicketInvalid(MfaError):
    """MFA ticket is missing, expired, bound to another request, or malformed."""


class MfaVerificationFailed(MfaError):
    """Presented MFA proof is invalid."""


@dataclass(frozen=True)
class MfaTicket:
    token: str
    user_id: str
    purpose: str
    role: str
    institution_id: str | None
    location_id: str | None
    audit_request_id: str
    revoke_existing: bool = False
    post_password_action: str | None = None
    challenge: str | None = None
    challenge_type: str | None = None
    pending_totp_secret: str | None = None


@dataclass(frozen=True)
class MfaStatus:
    webauthn_count: int
    totp_enabled: bool
    recovery_codes_remaining: int

    def enrolled_for_role(self, role: str) -> bool:
        if role == UserRole.SUPER_ADMIN.value:
            return self.webauthn_count > 0
        return self.webauthn_count > 0 or self.totp_enabled

    def available_methods_for_role(self, role: str) -> list[str]:
        methods: list[str] = []
        if self.webauthn_count > 0:
            methods.append("webauthn")
        if self.totp_enabled and role != UserRole.SUPER_ADMIN.value:
            methods.append("totp")
        if self.recovery_codes_remaining > 0:
            methods.append("recovery_code")
        return methods

    def setup_methods_for_role(self, role: str) -> list[str]:
        if role == UserRole.SUPER_ADMIN.value:
            return ["webauthn"]
        return ["webauthn", "totp"]


class MfaTicketService:
    """Short-lived Redis tickets for the password-accepted MFA step."""

    TICKET_PREFIX = "mfa_ticket"
    TTL_SECONDS = 10 * 60

    @classmethod
    def _key(cls, token: str) -> str:
        return f"{cls.TICKET_PREFIX}:{PasswordService.hash_token(token)}"

    @staticmethod
    def _request_hashes(*, client_ip: str | None, user_agent: str | None) -> dict[str, str]:
        return {
            "client_ip_hash": keyed_hash(client_ip or "", purpose="mfa-ticket-ip"),
            "user_agent_hash": keyed_hash(user_agent or "", purpose="mfa-ticket-ua"),
        }

    @staticmethod
    def _ticket_from_data(token: str, data: dict[str, Any]) -> MfaTicket:
        return MfaTicket(
            token=token,
            user_id=str(data["user_id"]),
            purpose=str(data["purpose"]),
            role=str(data["role"]),
            institution_id=data.get("institution_id"),
            location_id=data.get("location_id"),
            audit_request_id=str(data["audit_request_id"]),
            revoke_existing=bool(data.get("revoke_existing", False)),
            post_password_action=data.get("post_password_action"),
            challenge=data.get("challenge"),
            challenge_type=data.get("challenge_type"),
            pending_totp_secret=data.get("pending_totp_secret"),
        )

    @classmethod
    async def create(
        cls,
        *,
        user: User,
        purpose: str,
        client_ip: str | None,
        user_agent: str | None,
        audit_request_id: str,
        revoke_existing: bool = False,
        post_password_action: str | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        payload = {
            "user_id": str(user.id),
            "purpose": purpose,
            "role": user.role,
            "institution_id": str(user.institution_id) if user.institution_id else None,
            "location_id": str(user.location_id) if user.location_id else None,
            "audit_request_id": audit_request_id,
            "revoke_existing": revoke_existing,
            "post_password_action": post_password_action,
            **cls._request_hashes(client_ip=client_ip, user_agent=user_agent),
        }
        try:
            client = await RefreshTokenService.get_client()
            await client.setex(cls._key(token), cls.TTL_SECONDS, json.dumps(payload))
        except Exception as exc:  # noqa: BLE001
            raise MfaStoreUnavailable("MFA ticket store is unavailable") from exc
        return token

    @classmethod
    async def get(
        cls,
        token: str,
        *,
        client_ip: str | None,
        user_agent: str | None,
        purpose: str | None = None,
    ) -> MfaTicket:
        try:
            client = await RefreshTokenService.get_client()
            raw = await client.get(cls._key(token))
        except Exception as exc:  # noqa: BLE001
            raise MfaStoreUnavailable("MFA ticket store is unavailable") from exc
        if not raw:
            raise MfaTicketInvalid("Invalid or expired MFA ticket")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MfaTicketInvalid("Invalid MFA ticket") from exc

        expected_hashes = cls._request_hashes(client_ip=client_ip, user_agent=user_agent)
        if (
            data.get("client_ip_hash") != expected_hashes["client_ip_hash"]
            or data.get("user_agent_hash") != expected_hashes["user_agent_hash"]
        ):
            raise MfaTicketInvalid("MFA ticket does not match this request")
        if purpose is not None and data.get("purpose") != purpose:
            raise MfaTicketInvalid("MFA ticket has the wrong purpose")

        return cls._ticket_from_data(token, data)

    @classmethod
    async def update(cls, ticket: MfaTicket, **fields: Any) -> MfaTicket:
        try:
            client = await RefreshTokenService.get_client()
            raw = await client.get(cls._key(ticket.token))
        except Exception as exc:  # noqa: BLE001
            raise MfaStoreUnavailable("MFA ticket store is unavailable") from exc
        if not raw:
            raise MfaTicketInvalid("Invalid or expired MFA ticket")
        data = json.loads(raw)
        data.update(fields)
        await client.setex(cls._key(ticket.token), cls.TTL_SECONDS, json.dumps(data))
        return cls._ticket_from_data(ticket.token, data)

    @classmethod
    async def consume(cls, ticket: MfaTicket) -> None:
        try:
            client = await RefreshTokenService.get_client()
            await client.delete(cls._key(ticket.token))
        except Exception as exc:  # noqa: BLE001
            raise MfaStoreUnavailable("MFA ticket store is unavailable") from exc


class MfaService:
    RECOVERY_CODE_COUNT = 10
    RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    def __init__(self, session: AsyncSession):
        self.session = session

    async def status_for_user(self, user_id: str) -> MfaStatus:
        webauthn_count = await self.session.scalar(
            select(func.count(WebAuthnCredential.id)).where(WebAuthnCredential.user_id == user_id)
        )
        totp_count = await self.session.scalar(
            select(func.count(UserTotpFactor.id)).where(UserTotpFactor.user_id == user_id)
        )
        recovery_count = await self.session.scalar(
            select(func.count(MfaRecoveryCode.id)).where(
                MfaRecoveryCode.user_id == user_id,
                MfaRecoveryCode.used_at.is_(None),
            )
        )
        return MfaStatus(
            webauthn_count=int(webauthn_count or 0),
            totp_enabled=bool(totp_count),
            recovery_codes_remaining=int(recovery_count or 0),
        )

    async def webauthn_credentials(self, user_id: str) -> list[WebAuthnCredential]:
        rows = await self.session.execute(
            select(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id)
        )
        return list(rows.scalars().all())

    async def generate_webauthn_registration_options(
        self,
        *,
        user: User,
    ) -> tuple[dict[str, Any], str]:
        credentials = await self.webauthn_credentials(str(user.id))
        options = generate_registration_options(
            rp_id=settings.effective_webauthn_rp_id,
            rp_name=settings.webauthn_rp_name,
            user_name=user.email,
            user_id=str(user.id).encode("utf-8"),
            user_display_name=user.email,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(credential.credential_id))
                for credential in credentials
            ],
        )
        return json.loads(options_to_json(options)), bytes_to_base64url(options.challenge)

    async def verify_webauthn_registration(
        self,
        *,
        user: User,
        credential: dict[str, Any],
        expected_challenge: str,
        device_label: str | None,
    ) -> WebAuthnCredential:
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(expected_challenge),
                expected_rp_id=settings.effective_webauthn_rp_id,
                expected_origin=settings.effective_webauthn_allowed_origins,
                require_user_verification=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise MfaVerificationFailed("Passkey registration failed") from exc

        credential_id = bytes_to_base64url(verified.credential_id)
        existing = await self.session.scalar(
            select(WebAuthnCredential).where(WebAuthnCredential.credential_id == credential_id)
        )
        if existing:
            raise MfaVerificationFailed("Passkey is already registered")

        transports = credential.get("response", {}).get("transports")
        if not isinstance(transports, list):
            transports = None

        row = WebAuthnCredential(
            user_id=str(user.id),
            credential_id=credential_id,
            public_key=bytes_to_base64url(verified.credential_public_key),
            sign_count=verified.sign_count,
            transports=[str(t) for t in transports] if transports else None,
            device_label=(device_label or "Passkey")[:120],
            aaguid=verified.aaguid,
            credential_device_type=getattr(verified.credential_device_type, "value", None),
            credential_backed_up=bool(verified.credential_backed_up),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def generate_webauthn_authentication_options(
        self,
        *,
        user_id: str,
    ) -> tuple[dict[str, Any], str]:
        credentials = await self.webauthn_credentials(user_id)
        if not credentials:
            raise MfaVerificationFailed("No passkey is registered")
        descriptors: list[PublicKeyCredentialDescriptor] = []
        for credential in credentials:
            transports = None
            if credential.transports:
                transports = [AuthenticatorTransport(value) for value in credential.transports]
            descriptors.append(
                PublicKeyCredentialDescriptor(
                    id=base64url_to_bytes(credential.credential_id),
                    transports=transports,
                )
            )
        options = generate_authentication_options(
            rp_id=settings.effective_webauthn_rp_id,
            allow_credentials=descriptors,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        return json.loads(options_to_json(options)), bytes_to_base64url(options.challenge)

    async def verify_webauthn_authentication(
        self,
        *,
        user_id: str,
        credential: dict[str, Any],
        expected_challenge: str,
    ) -> WebAuthnCredential:
        credential_id = str(credential.get("id") or "")
        row = await self.session.scalar(
            select(WebAuthnCredential).where(
                WebAuthnCredential.user_id == user_id,
                WebAuthnCredential.credential_id == credential_id,
            )
        )
        if not row:
            raise MfaVerificationFailed("Passkey is not registered for this user")

        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(expected_challenge),
                expected_rp_id=settings.effective_webauthn_rp_id,
                expected_origin=settings.effective_webauthn_allowed_origins,
                credential_public_key=base64url_to_bytes(row.public_key),
                credential_current_sign_count=row.sign_count,
                require_user_verification=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise MfaVerificationFailed("Passkey verification failed") from exc

        if row.sign_count > 0 and verified.new_sign_count <= row.sign_count:
            raise MfaVerificationFailed("Passkey sign counter moved backwards")

        row.sign_count = verified.new_sign_count
        row.last_used_at = datetime.now(timezone.utc)
        await self.session.flush()
        return row

    @staticmethod
    def new_totp_secret() -> str:
        return pyotp.random_base32()

    @staticmethod
    def totp_uri(*, secret: str, email: str) -> str:
        return pyotp.TOTP(secret).provisioning_uri(
            name=email,
            issuer_name=settings.webauthn_rp_name,
        )

    async def verify_and_store_totp_setup(self, *, user_id: str, secret: str, code: str) -> UserTotpFactor:
        if not self._verify_totp_code(secret=secret, code=code):
            raise MfaVerificationFailed("Invalid authenticator code")
        row = await self.session.scalar(
            select(UserTotpFactor).where(UserTotpFactor.user_id == user_id)
        )
        if row is None:
            row = UserTotpFactor(user_id=user_id)
            self.session.add(row)
        row.secret = secret
        row.last_accepted_time_step = self._matching_totp_time_step(secret=secret, code=code)
        await self.session.flush()
        return row

    async def verify_totp(self, *, user_id: str, code: str) -> UserTotpFactor:
        row = await self.session.scalar(
            select(UserTotpFactor).where(UserTotpFactor.user_id == user_id)
        )
        if row is None:
            raise MfaVerificationFailed("No authenticator app is registered")
        time_step = self._matching_totp_time_step(secret=row.secret, code=code)
        if time_step is None:
            raise MfaVerificationFailed("Invalid authenticator code")
        if row.last_accepted_time_step is not None and time_step <= row.last_accepted_time_step:
            raise MfaVerificationFailed("Authenticator code was already used")
        row.last_accepted_time_step = time_step
        await self.session.flush()
        return row

    @staticmethod
    def _matching_totp_time_step(*, secret: str, code: str) -> int | None:
        normalized = "".join(ch for ch in code if ch.isdigit())
        if len(normalized) != 6:
            return None
        totp = pyotp.TOTP(secret)
        now = datetime.now(timezone.utc)
        for offset in (-1, 0, 1):
            candidate_time = now + timedelta(seconds=offset * totp.interval)
            if pyotp.utils.strings_equal(totp.at(candidate_time), normalized):
                return int(totp.timecode(candidate_time))
        return None

    @classmethod
    def _verify_totp_code(cls, *, secret: str, code: str) -> bool:
        return cls._matching_totp_time_step(secret=secret, code=code) is not None

    @classmethod
    def generate_recovery_code_plaintexts(cls) -> list[str]:
        codes: list[str] = []
        for _ in range(cls.RECOVERY_CODE_COUNT):
            raw = "".join(secrets.choice(cls.RECOVERY_ALPHABET) for _ in range(15))
            codes.append(f"{raw[:5]}-{raw[5:10]}-{raw[10:]}")
        return codes

    @staticmethod
    def normalize_recovery_code(code: str) -> str:
        return "".join(ch for ch in code.upper() if ch.isalnum())

    async def replace_recovery_codes(self, *, user_id: str) -> list[str]:
        codes = self.generate_recovery_code_plaintexts()
        await self.session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
        for code in codes:
            self.session.add(
                MfaRecoveryCode(
                    user_id=user_id,
                    code_hash=PasswordService.hash_secret(self.normalize_recovery_code(code)),
                )
            )
        await self.session.flush()
        return codes

    async def ensure_recovery_codes(self, *, user_id: str) -> list[str]:
        remaining = await self.session.scalar(
            select(func.count(MfaRecoveryCode.id)).where(MfaRecoveryCode.user_id == user_id)
        )
        if int(remaining or 0) > 0:
            return []
        return await self.replace_recovery_codes(user_id=user_id)

    async def use_recovery_code(self, *, user_id: str, code: str) -> MfaRecoveryCode:
        normalized = self.normalize_recovery_code(code)
        rows = await self.session.execute(
            select(MfaRecoveryCode).where(
                MfaRecoveryCode.user_id == user_id,
                MfaRecoveryCode.used_at.is_(None),
            )
        )
        for row in rows.scalars().all():
            if PasswordService.verify_secret(normalized, row.code_hash):
                row.used_at = datetime.now(timezone.utc)
                await self.session.flush()
                return row
        # Keep failed attempts closer in timing to successful checks.
        PasswordService.verify_secret(normalized or "invalid", PasswordService.hash_secret(secrets.token_urlsafe(16)))
        raise MfaVerificationFailed("Invalid recovery code")

    @staticmethod
    def auth_time_now() -> int:
        return int(time.time())
