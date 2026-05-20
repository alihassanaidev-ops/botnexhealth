"""MFA services for Redis tickets, WebAuthn/passkeys, TOTP, and recovery codes."""

from __future__ import annotations

import ipaddress
import json
import logging
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

logger = logging.getLogger(__name__)


def _log_hash(value: Any) -> str:
    if value is None or value == "":
        return "none"
    return keyed_hash(str(value), purpose="mfa-log-hash-v1", truncate_hex=16)


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
    # `elevated=True` on a `purpose='step_up'` ticket marks that the user
    # has freshly re-verified their MFA factor and the ticket may be
    # presented to a sensitive factor-management endpoint exactly once.
    # See MfaTicketService.consume_step_up.
    elevated: bool = False


# Purposes are stamped on a ticket at creation and locked thereafter. They
# fence each MFA-protected flow off from the others — a step-up ticket
# (proves fresh verification for a sensitive operation) must not be
# usable to log in, and a login ticket must not be usable to authorise a
# passkey deletion.
MFA_PURPOSE_LOGIN = "login"
MFA_PURPOSE_RESET_PASSWORD = "reset_password"
MFA_PURPOSE_SET_PASSWORD = "set_password"
MFA_PURPOSE_STEP_UP = "step_up"

# Add-factor enrollment tickets. After a step-up ticket is consumed,
# the add-factor endpoints issue one of these to carry the in-progress
# WebAuthn challenge (or pending TOTP secret) across the two HTTP round
# trips needed to enrol a new factor. Short TTL (5 min) and single-use
# at the verify call; the user is already authenticated and step-up'd
# so leaking one only enables a credential the user themselves was
# about to register.
MFA_PURPOSE_ADD_FACTOR_WEBAUTHN = "add_factor_webauthn"
MFA_PURPOSE_ADD_FACTOR_TOTP = "add_factor_totp"

ADD_FACTOR_TICKET_TTL_SECONDS = 5 * 60

# Step-up tickets shorten the freshness window once verified; the
# elevated form is single-use and short-lived to bound replay risk after
# the user finishes verifying.
STEP_UP_ELEVATED_TTL_SECONDS = 90


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

    @classmethod
    def _kid(cls, token: str) -> str:
        # Short, non-reversible ticket identifier safe for log correlation.
        return PasswordService.hash_token(token)[:12]

    @staticmethod
    def _network_prefix(client_ip: str | None) -> str:
        """Reduce a client IP to its network prefix for ticket binding.

        Binds to /24 for IPv4 and /64 for IPv6 instead of the full
        address. Catches cross-network ticket replay (different ISP, AS,
        or country) while tolerating same-network NAT-pool egress
        rotation, which is common behind corporate VPNs / proxies hosted
        on cloud providers (AWS/GCP/Azure outbound NAT round-robins
        across multiple egress IPs in the same /24).
        """
        if not client_ip:
            return ""
        try:
            addr = ipaddress.ip_address(client_ip)
        except ValueError:
            return client_ip
        prefix = 24 if isinstance(addr, ipaddress.IPv4Address) else 64
        return str(ipaddress.ip_network(f"{client_ip}/{prefix}", strict=False))

    @classmethod
    def _request_hashes(
        cls, *, client_ip: str | None, user_agent: str | None
    ) -> dict[str, str]:
        return {
            "client_ip_hash": keyed_hash(
                cls._network_prefix(client_ip), purpose="mfa-ticket-ip"
            ),
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
            elevated=bool(data.get("elevated", False)),
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
        ttl_seconds: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Create a short-lived MFA ticket.

        ``ttl_seconds`` defaults to TTL_SECONDS (10 min — the long
        login-flow window). Callers issuing tickets for tighter flows
        (add-factor enrollment, step-up elevation) can pass a shorter
        TTL. ``extra`` carries flow-specific fields (e.g. a pending
        WebAuthn challenge or TOTP secret) that ``_ticket_from_data``
        reads back into the typed MfaTicket.
        """
        token = secrets.token_urlsafe(32)
        hashes = cls._request_hashes(client_ip=client_ip, user_agent=user_agent)
        payload: dict[str, Any] = {
            "user_id": str(user.id),
            "purpose": purpose,
            "role": user.role,
            "institution_id": str(user.institution_id) if user.institution_id else None,
            "location_id": str(user.location_id) if user.location_id else None,
            "audit_request_id": audit_request_id,
            "revoke_existing": revoke_existing,
            "post_password_action": post_password_action,
            **hashes,
        }
        if extra:
            payload.update(extra)
        try:
            client = await RefreshTokenService.get_client()
            await client.setex(
                cls._key(token),
                ttl_seconds if ttl_seconds is not None else cls.TTL_SECONDS,
                json.dumps(payload),
            )
        except Exception as exc:  # noqa: BLE001
            raise MfaStoreUnavailable("MFA ticket store is unavailable") from exc
        logger.info(
            "mfa_ticket_created kid=%s purpose=%s user_hash=%s role=%s "
            "ttl=%s ip_hash=%s ua_hash=%s "
            "has_challenge=%s",
            cls._kid(token),
            purpose,
            _log_hash(user.id),
            user.role,
            ttl_seconds if ttl_seconds is not None else cls.TTL_SECONDS,
            hashes["client_ip_hash"][:12],
            hashes["user_agent_hash"][:12],
            bool(extra and extra.get("challenge")),
        )
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
        kid = cls._kid(token)
        try:
            client = await RefreshTokenService.get_client()
            raw = await client.get(cls._key(token))
        except Exception as exc:  # noqa: BLE001
            raise MfaStoreUnavailable("MFA ticket store is unavailable") from exc
        if not raw:
            logger.warning(
                "mfa_ticket_invalid reason=missing_or_expired kid=%s "
                "expected_purpose=%s",
                kid,
                purpose,
            )
            raise MfaTicketInvalid("Invalid or expired MFA ticket")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("mfa_ticket_invalid reason=malformed_json kid=%s", kid)
            raise MfaTicketInvalid("Invalid MFA ticket") from exc

        expected_hashes = cls._request_hashes(
            client_ip=client_ip, user_agent=user_agent
        )
        if (
            data.get("client_ip_hash") != expected_hashes["client_ip_hash"]
            or data.get("user_agent_hash") != expected_hashes["user_agent_hash"]
        ):
            logger.warning(
                "mfa_ticket_invalid reason=fingerprint_mismatch kid=%s purpose=%s "
                "user_hash=%s "
                "stored_ip_hash=%s expected_ip_hash=%s ip_match=%s "
                "stored_ua_hash=%s expected_ua_hash=%s ua_match=%s",
                kid,
                data.get("purpose"),
                _log_hash(data.get("user_id")),
                (data.get("client_ip_hash") or "")[:12],
                expected_hashes["client_ip_hash"][:12],
                data.get("client_ip_hash") == expected_hashes["client_ip_hash"],
                (data.get("user_agent_hash") or "")[:12],
                expected_hashes["user_agent_hash"][:12],
                data.get("user_agent_hash") == expected_hashes["user_agent_hash"],
            )
            raise MfaTicketInvalid("MFA ticket does not match this request")
        if purpose is not None and data.get("purpose") != purpose:
            logger.warning(
                "mfa_ticket_invalid reason=wrong_purpose kid=%s "
                "expected_purpose=%s got_purpose=%s user_hash=%s",
                kid,
                purpose,
                data.get("purpose"),
                _log_hash(data.get("user_id")),
            )
            raise MfaTicketInvalid("MFA ticket has the wrong purpose")

        logger.info(
            "mfa_ticket_validated kid=%s purpose=%s user_hash=%s via=get",
            kid,
            data.get("purpose"),
            _log_hash(data.get("user_id")),
        )
        return cls._ticket_from_data(token, data)

    @classmethod
    async def update(cls, ticket: MfaTicket, **fields: Any) -> MfaTicket:
        kid = cls._kid(ticket.token)
        try:
            client = await RefreshTokenService.get_client()
            raw = await client.get(cls._key(ticket.token))
        except Exception as exc:  # noqa: BLE001
            raise MfaStoreUnavailable("MFA ticket store is unavailable") from exc
        if not raw:
            logger.warning(
                "mfa_ticket_invalid reason=missing_or_expired kid=%s via=update",
                kid,
            )
            raise MfaTicketInvalid("Invalid or expired MFA ticket")
        data = json.loads(raw)
        data.update(fields)
        await client.setex(cls._key(ticket.token), cls.TTL_SECONDS, json.dumps(data))
        logger.info(
            "mfa_ticket_updated kid=%s purpose=%s user_hash=%s fields=%s",
            kid,
            data.get("purpose"),
            _log_hash(data.get("user_id")),
            list(fields.keys()),
        )
        return cls._ticket_from_data(ticket.token, data)

    @classmethod
    async def consume(cls, ticket: MfaTicket) -> None:
        try:
            client = await RefreshTokenService.get_client()
            await client.delete(cls._key(ticket.token))
        except Exception as exc:  # noqa: BLE001
            raise MfaStoreUnavailable("MFA ticket store is unavailable") from exc

    @classmethod
    async def mark_step_up_elevated(cls, ticket: MfaTicket) -> MfaTicket:
        """Mark a step-up ticket as freshly verified.

        Called from the step-up verify endpoints after the user proves a
        factor. The same ticket token is then accepted exactly once by a
        factor-management endpoint within ``STEP_UP_ELEVATED_TTL_SECONDS``.
        """
        kid = cls._kid(ticket.token)
        if ticket.purpose != MFA_PURPOSE_STEP_UP:
            logger.warning(
                "mfa_ticket_invalid reason=cannot_elevate kid=%s purpose=%s",
                kid,
                ticket.purpose,
            )
            raise MfaTicketInvalid("Only step-up tickets can be elevated")
        try:
            client = await RefreshTokenService.get_client()
            raw = await client.get(cls._key(ticket.token))
            if not raw:
                logger.warning(
                    "mfa_ticket_invalid reason=missing_or_expired kid=%s via=mark_elevated",
                    kid,
                )
                raise MfaTicketInvalid("Invalid or expired MFA ticket")
            data = json.loads(raw)
            data["elevated"] = True
            await client.setex(
                cls._key(ticket.token),
                STEP_UP_ELEVATED_TTL_SECONDS,
                json.dumps(data),
            )
        except MfaError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MfaStoreUnavailable("MFA ticket store is unavailable") from exc
        logger.info(
            "mfa_ticket_elevated kid=%s user_hash=%s ttl=%s",
            kid,
            _log_hash(data.get("user_id")),
            STEP_UP_ELEVATED_TTL_SECONDS,
        )
        return cls._ticket_from_data(ticket.token, data)

    @classmethod
    async def _atomic_take(cls, token: str) -> dict[str, Any]:
        """Atomic GETDEL on the ticket's Redis key.

        Returns the decoded payload exactly once across all callers —
        the second observer sees a deleted key and raises
        MfaTicketInvalid. All subsequent validation happens against the
        in-memory dict, so even a bad-validation request still consumes
        the ticket. Single-use semantics for every flow that uses this
        helper.
        """
        kid = cls._kid(token)
        try:
            client = await RefreshTokenService.get_client()
            raw = await client.getdel(cls._key(token))
        except Exception as exc:  # noqa: BLE001
            raise MfaStoreUnavailable("MFA ticket store is unavailable") from exc
        if not raw:
            logger.warning(
                "mfa_ticket_invalid reason=missing_or_already_consumed kid=%s via=atomic_take",
                kid,
            )
            raise MfaTicketInvalid("Invalid or expired MFA ticket")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(
                "mfa_ticket_invalid reason=malformed_json kid=%s via=atomic_take", kid
            )
            raise MfaTicketInvalid("Invalid MFA ticket") from exc

    @classmethod
    async def consume_enrollment_ticket(
        cls,
        token: str,
        *,
        user_id: str,
        expected_purpose: str,
        client_ip: str | None,
        user_agent: str | None,
    ) -> MfaTicket:
        """Atomically validate and consume an add-factor enrollment ticket.

        Same Redis pattern as ``consume_step_up`` but enforces the
        add-factor purpose family rather than ``step_up``. Used by
        ``/auth/mfa/factors/{webauthn,totp}/*/verify`` to ensure the
        challenge-bound enrollment ticket issued by the matching
        ``/options`` endpoint can only be presented once.
        """
        kid = cls._kid(token)
        data = await cls._atomic_take(token)
        expected_hashes = cls._request_hashes(
            client_ip=client_ip, user_agent=user_agent
        )
        if (
            data.get("client_ip_hash") != expected_hashes["client_ip_hash"]
            or data.get("user_agent_hash") != expected_hashes["user_agent_hash"]
        ):
            logger.warning(
                "mfa_ticket_invalid reason=fingerprint_mismatch kid=%s via=consume_enrollment "
                "expected_purpose=%s got_purpose=%s user_hash=%s "
                "stored_ip_hash=%s expected_ip_hash=%s ip_match=%s "
                "stored_ua_hash=%s expected_ua_hash=%s ua_match=%s",
                kid,
                expected_purpose,
                data.get("purpose"),
                _log_hash(data.get("user_id")),
                (data.get("client_ip_hash") or "")[:12],
                expected_hashes["client_ip_hash"][:12],
                data.get("client_ip_hash") == expected_hashes["client_ip_hash"],
                (data.get("user_agent_hash") or "")[:12],
                expected_hashes["user_agent_hash"][:12],
                data.get("user_agent_hash") == expected_hashes["user_agent_hash"],
            )
            raise MfaTicketInvalid("MFA ticket does not match this request")
        if data.get("purpose") != expected_purpose:
            logger.warning(
                "mfa_ticket_invalid reason=wrong_purpose kid=%s via=consume_enrollment "
                "expected_purpose=%s got_purpose=%s user_hash=%s",
                kid,
                expected_purpose,
                data.get("purpose"),
                _log_hash(data.get("user_id")),
            )
            raise MfaTicketInvalid("MFA ticket has the wrong purpose")
        if data.get("user_id") != str(user_id):
            logger.warning(
                "mfa_ticket_invalid reason=user_mismatch kid=%s via=consume_enrollment "
                "ticket_user_hash=%s session_user_hash=%s",
                kid,
                _log_hash(data.get("user_id")),
                _log_hash(user_id),
            )
            raise MfaTicketInvalid("Enrollment ticket does not match the current user")
        logger.info(
            "mfa_ticket_validated kid=%s purpose=%s user_hash=%s via=consume_enrollment",
            kid,
            data.get("purpose"),
            _log_hash(data.get("user_id")),
        )
        return cls._ticket_from_data(token, data)

    @classmethod
    async def consume_step_up(
        cls,
        token: str,
        *,
        user_id: str,
        client_ip: str | None,
        user_agent: str | None,
    ) -> MfaTicket:
        """Atomically validate-and-consume a step-up ticket.

        Used at the entry point of every factor-management endpoint. The
        ticket must be:

          - bound to ``user_id`` (the currently-authenticated user),
          - issued with ``purpose=MFA_PURPOSE_STEP_UP``,
          - already through the step-up verify flow (``elevated=True``),
          - still within its TTL,
          - matching the request's IP+UA fingerprint.

        Atomicity matters: a naive ``GET`` + Python check + ``DEL`` lets
        two concurrent destructive requests both read the elevated
        ticket before either ``DEL`` lands, and both proceed. Redis
        ``GETDEL`` (server-side) returns the value and deletes the key
        in a single round-trip, so exactly one caller observes a
        non-nil payload. We fail-closed on every other branch: a
        ``GETDEL`` that returns a payload but fails downstream
        validation still consumes the ticket — the user retries the
        flow from the challenge step, which is the right UX for a
        suspected-tampering case.
        """
        kid = cls._kid(token)
        data = await cls._atomic_take(token)

        # All subsequent validation happens on the in-memory payload —
        # the Redis side already proved exclusivity by deleting the
        # key. Each predicate fails closed; we never re-write the
        # ticket on failure.
        expected_hashes = cls._request_hashes(
            client_ip=client_ip, user_agent=user_agent
        )
        if (
            data.get("client_ip_hash") != expected_hashes["client_ip_hash"]
            or data.get("user_agent_hash") != expected_hashes["user_agent_hash"]
        ):
            logger.warning(
                "mfa_ticket_invalid reason=fingerprint_mismatch kid=%s via=consume_step_up "
                "user_hash=%s "
                "stored_ip_hash=%s expected_ip_hash=%s ip_match=%s "
                "stored_ua_hash=%s expected_ua_hash=%s ua_match=%s",
                kid,
                _log_hash(data.get("user_id")),
                (data.get("client_ip_hash") or "")[:12],
                expected_hashes["client_ip_hash"][:12],
                data.get("client_ip_hash") == expected_hashes["client_ip_hash"],
                (data.get("user_agent_hash") or "")[:12],
                expected_hashes["user_agent_hash"][:12],
                data.get("user_agent_hash") == expected_hashes["user_agent_hash"],
            )
            raise MfaTicketInvalid("MFA ticket does not match this request")
        if data.get("purpose") != MFA_PURPOSE_STEP_UP:
            logger.warning(
                "mfa_ticket_invalid reason=wrong_purpose kid=%s via=consume_step_up "
                "expected_purpose=%s got_purpose=%s user_hash=%s",
                kid,
                MFA_PURPOSE_STEP_UP,
                data.get("purpose"),
                _log_hash(data.get("user_id")),
            )
            raise MfaTicketInvalid("MFA ticket has the wrong purpose")
        if data.get("user_id") != str(user_id):
            logger.warning(
                "mfa_ticket_invalid reason=user_mismatch kid=%s via=consume_step_up "
                "ticket_user_hash=%s session_user_hash=%s",
                kid,
                _log_hash(data.get("user_id")),
                _log_hash(user_id),
            )
            raise MfaTicketInvalid("Step-up ticket does not match the current user")
        if not data.get("elevated"):
            logger.warning(
                "mfa_ticket_invalid reason=not_elevated kid=%s via=consume_step_up user_hash=%s",
                kid,
                _log_hash(data.get("user_id")),
            )
            raise MfaTicketInvalid("Step-up verification has not been completed")
        logger.info(
            "mfa_ticket_validated kid=%s purpose=step_up user_hash=%s via=consume_step_up",
            kid,
            _log_hash(data.get("user_id")),
        )
        return cls._ticket_from_data(token, data)


class MfaService:
    RECOVERY_CODE_COUNT = 10
    RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    def __init__(self, session: AsyncSession):
        self.session = session

    async def status_for_user(self, user_id: str) -> MfaStatus:
        webauthn_count = await self.session.scalar(
            select(func.count(WebAuthnCredential.id)).where(
                WebAuthnCredential.user_id == user_id
            )
        )
        totp_count = await self.session.scalar(
            select(func.count(UserTotpFactor.id)).where(
                UserTotpFactor.user_id == user_id
            )
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
        # UV strictness is config-driven so deployments with older
        # non-UV-capable security keys can opt down. Default is
        # REQUIRED — the authenticator must prove inherence (biometric
        # or PIN) before signing, which collapses the "passkey is a
        # full factor" promise back to "something you have" if relaxed.
        uv_requirement = (
            UserVerificationRequirement.REQUIRED
            if settings.webauthn_user_verification_strict
            else UserVerificationRequirement.PREFERRED
        )
        options = generate_registration_options(
            rp_id=settings.effective_webauthn_rp_id,
            rp_name=settings.webauthn_rp_name,
            user_name=user.email,
            user_id=str(user.id).encode("utf-8"),
            user_display_name=user.email,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=uv_requirement,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(
                    id=base64url_to_bytes(credential.credential_id)
                )
                for credential in credentials
            ],
        )
        return json.loads(options_to_json(options)), bytes_to_base64url(
            options.challenge
        )

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
                require_user_verification=settings.webauthn_user_verification_strict,
            )
        except Exception as exc:  # noqa: BLE001
            raise MfaVerificationFailed("Passkey registration failed") from exc

        credential_id = bytes_to_base64url(verified.credential_id)
        existing = await self.session.scalar(
            select(WebAuthnCredential).where(
                WebAuthnCredential.credential_id == credential_id
            )
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
            credential_device_type=getattr(
                verified.credential_device_type, "value", None
            ),
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
                transports = [
                    AuthenticatorTransport(value) for value in credential.transports
                ]
            descriptors.append(
                PublicKeyCredentialDescriptor(
                    id=base64url_to_bytes(credential.credential_id),
                    transports=transports,
                )
            )
        # Pair the authentication-side UV setting with the same config
        # the registration path uses so a key enrolled with UV must
        # also use UV to authenticate (and vice versa for the relaxed
        # mode). Mixing strict registration with relaxed authentication
        # would let a stolen security key authenticate without UV
        # despite the user thinking they enrolled a "real" passkey.
        uv_requirement = (
            UserVerificationRequirement.REQUIRED
            if settings.webauthn_user_verification_strict
            else UserVerificationRequirement.PREFERRED
        )
        options = generate_authentication_options(
            rp_id=settings.effective_webauthn_rp_id,
            allow_credentials=descriptors,
            user_verification=uv_requirement,
        )
        return json.loads(options_to_json(options)), bytes_to_base64url(
            options.challenge
        )

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
                require_user_verification=settings.webauthn_user_verification_strict,
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

    async def verify_and_store_totp_setup(
        self, *, user_id: str, secret: str, code: str
    ) -> UserTotpFactor:
        if not self._verify_totp_code(secret=secret, code=code):
            raise MfaVerificationFailed("Invalid authenticator code")
        row = await self.session.scalar(
            select(UserTotpFactor).where(UserTotpFactor.user_id == user_id)
        )
        if row is None:
            row = UserTotpFactor(user_id=user_id)
            self.session.add(row)
        row.secret = secret
        row.last_accepted_time_step = self._matching_totp_time_step(
            secret=secret, code=code
        )
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
        if (
            row.last_accepted_time_step is not None
            and time_step <= row.last_accepted_time_step
        ):
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
        await self.session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        )
        for code in codes:
            self.session.add(
                MfaRecoveryCode(
                    user_id=user_id,
                    code_hash=PasswordService.hash_secret(
                        self.normalize_recovery_code(code)
                    ),
                )
            )
        await self.session.flush()
        return codes

    async def ensure_recovery_codes(self, *, user_id: str) -> list[str]:
        remaining = await self.session.scalar(
            select(func.count(MfaRecoveryCode.id)).where(
                MfaRecoveryCode.user_id == user_id
            )
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
        PasswordService.verify_secret(
            normalized or "invalid",
            PasswordService.hash_secret(secrets.token_urlsafe(16)),
        )
        raise MfaVerificationFailed("Invalid recovery code")

    @staticmethod
    def auth_time_now() -> int:
        return int(time.time())

    # =========================================================================
    # Factor management — operational completeness (lost device, swap phone).
    # The runtime auth flow (login + MFA challenge) handles re-enrollment on
    # the next login when a user has zero factors, so removing a factor never
    # bricks the account: the next login simply returns mfa_setup_required.
    # =========================================================================

    async def remove_webauthn_credential(
        self, *, user_id: str, credential_pk: str
    ) -> WebAuthnCredential | None:
        """Remove a passkey owned by the user. Returns the deleted row or None.

        Filtering on ``user_id`` defends against IDOR even with RLS, so a
        coding mistake that drops the RLS context still cannot delete
        another user's credential.
        """
        row = await self.session.scalar(
            select(WebAuthnCredential).where(
                WebAuthnCredential.id == credential_pk,
                WebAuthnCredential.user_id == user_id,
            )
        )
        if row is None:
            return None
        await self.session.delete(row)
        await self.session.flush()
        return row

    async def disable_totp(self, *, user_id: str) -> bool:
        """Remove any registered TOTP factor for the user.

        Returns True if a row was deleted. Idempotent — disabling when no
        factor exists returns False without error.
        """
        row = await self.session.scalar(
            select(UserTotpFactor).where(UserTotpFactor.user_id == user_id)
        )
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def wipe_all_factors(self, *, user_id: str) -> dict[str, int]:
        """Break-glass: remove every MFA factor for a user.

        Returns a per-factor counter so the audit row can record exactly
        what was destroyed. Intended for super-admin recovery when a
        user has lost every authenticator AND every recovery code —
        otherwise the user uses the normal Security UI which only
        removes one factor at a time and demands step-up.
        """
        webauthn_count = await self.session.scalar(
            select(func.count(WebAuthnCredential.id)).where(
                WebAuthnCredential.user_id == user_id,
            )
        )
        totp_count = await self.session.scalar(
            select(func.count(UserTotpFactor.id)).where(
                UserTotpFactor.user_id == user_id,
            )
        )
        recovery_count = await self.session.scalar(
            select(func.count(MfaRecoveryCode.id)).where(
                MfaRecoveryCode.user_id == user_id,
                MfaRecoveryCode.used_at.is_(None),
            )
        )
        await self.session.execute(
            delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id)
        )
        await self.session.execute(
            delete(UserTotpFactor).where(UserTotpFactor.user_id == user_id)
        )
        await self.session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        )
        await self.session.flush()
        return {
            "webauthn": int(webauthn_count or 0),
            "totp": int(totp_count or 0),
            "recovery_codes": int(recovery_count or 0),
        }
