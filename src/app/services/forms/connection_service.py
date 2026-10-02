"""Authorising a provider account, and the state that survives the round trip.

An OAuth redirect leaves our origin and comes back. Whatever we need on the
other side has to travel in ``state``, and ``state`` is attacker-controlled by
construction — the browser hands it back, and so could anybody else's browser.

So it is signed and it expires. It carries the institution, the user who started
it and the provider, which is what stops the classic cross-tenant confusion:
without it, a callback could be replayed into a different clinic's session and
attach somebody else's Facebook Page to their account.

Nothing here trusts the state's contents until the signature checks out, and the
institution in the verified state is checked against the caller's own before a
connection row is written.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
from src.app.models.form_integration import (
    FormConnectionStatus,
    FormProvider,
    FormProviderConnection,
)
from src.app.security import keyed_hash
from src.app.services.forms.providers import meta as meta_provider
from src.app.services.forms.providers import typeform as typeform_provider
from src.app.services.forms.providers.base import (
    FormProviderError,
    ProviderAccount,
)

logger = logging.getLogger(__name__)

_STATE_PURPOSE = "form-oauth-state-v1"

#: Long enough for somebody to read a Meta consent screen and pick a Page,
#: short enough that a state left in a browser history is not a live credential.
STATE_TTL_SECONDS = 900

#: Where both providers send the clinic back. One route for both, with the
#: provider named inside the signed state, so only a single redirect URI has to
#: be registered in each provider's app settings.
OAUTH_CALLBACK_PATH = "/institution-admin/form-integrations/callback"


def oauth_redirect_uri() -> str:
    base = (settings.public_base_url or "").rstrip("/")
    return f"{base}{OAUTH_CALLBACK_PATH}"


@dataclass(frozen=True)
class OAuthState:
    provider: str
    institution_id: str
    user_id: str
    issued_at: int


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def encode_state(
    *, provider: str, institution_id: str, user_id: str, now: datetime | None = None
) -> str:
    """A signed, self-contained state parameter."""
    issued_at = int((now or datetime.now(timezone.utc)).timestamp())
    payload = json.dumps(
        {
            "p": provider,
            "i": institution_id,
            "u": user_id,
            "t": issued_at,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    body = _b64(payload)
    return f"{body}.{keyed_hash(body, purpose=_STATE_PURPOSE)}"


def decode_state(state: str, *, now: datetime | None = None) -> OAuthState:
    """Verify and unpack. Raises rather than returning a partial result.

    Every failure mode gets the same message. A callback that says *why* it was
    rejected tells whoever sent it how to send a better one.
    """
    invalid = FormProviderError("This connection link is invalid or has expired.")
    try:
        body, signature = state.split(".", 1)
    except ValueError:
        raise invalid from None
    if not hmac.compare_digest(keyed_hash(body, purpose=_STATE_PURPOSE), signature):
        raise invalid
    try:
        payload = json.loads(_unb64(body))
    except Exception:  # noqa: BLE001 — malformed is invalid, no further detail
        raise invalid from None

    issued_at = int(payload.get("t") or 0)
    current = int((now or datetime.now(timezone.utc)).timestamp())
    if issued_at <= 0 or current - issued_at > STATE_TTL_SECONDS:
        raise invalid
    provider = str(payload.get("p") or "")
    institution_id = str(payload.get("i") or "")
    if provider not in {p.value for p in FormProvider} or not institution_id:
        raise invalid
    return OAuthState(
        provider=provider,
        institution_id=institution_id,
        user_id=str(payload.get("u") or ""),
        issued_at=issued_at,
    )


def provider_is_configured(provider: str) -> bool:
    if provider == FormProvider.META.value:
        return meta_provider.is_configured()
    if provider == FormProvider.TYPEFORM.value:
        return typeform_provider.is_configured()
    return False


def authorization_url(*, provider: str, state: str) -> str:
    redirect_uri = oauth_redirect_uri()
    if provider == FormProvider.META.value:
        return meta_provider.authorization_url(redirect_uri=redirect_uri, state=state)
    if provider == FormProvider.TYPEFORM.value:
        return typeform_provider.authorization_url(
            redirect_uri=redirect_uri, state=state
        )
    raise FormProviderError(f"Unknown provider: {provider}")


async def exchange_code_for_accounts(
    *, provider: str, code: str
) -> list[ProviderAccount]:
    """The authorised accounts behind one authorisation code.

    Meta returns several — a person may administer more than one Page, and we
    cannot know which one runs their lead ads, so all of them are connected and
    the clinic enables the forms it cares about. Typeform returns exactly one.
    """
    redirect_uri = oauth_redirect_uri()
    if provider == FormProvider.META.value:
        short_lived = await meta_provider.exchange_code(
            code=code, redirect_uri=redirect_uri
        )
        long_lived, expires_at = await meta_provider.exchange_long_lived(short_lived)
        accounts = await meta_provider.list_pages(long_lived)
        # Page tokens derived from a long-lived user token do not expire, but
        # the derivation does: recording the user token's expiry is what lets a
        # clinic be warned before every Page quietly stops syncing.
        return [
            ProviderAccount(
                account_ref=account.account_ref,
                account_name=account.account_name,
                access_token=account.access_token,
                token_expires_at=expires_at,
                granted_scopes=account.granted_scopes,
            )
            for account in accounts
        ]
    if provider == FormProvider.TYPEFORM.value:
        return [await typeform_provider.exchange_code(code=code, redirect_uri=redirect_uri)]
    raise FormProviderError(f"Unknown provider: {provider}")


async def upsert_connection(
    session: AsyncSession,
    *,
    institution_id: str,
    provider: str,
    account: ProviderAccount,
    user_id: str | None,
) -> FormProviderConnection:
    """Store, or refresh, one authorised account.

    Reconnecting an account already on file updates the token in place rather
    than making a second row: the forms, mappings and live workflows all hang
    off the existing connection, and a duplicate would strand them.
    """
    existing = (
        await session.execute(
            select(FormProviderConnection).where(
                FormProviderConnection.institution_id == institution_id,
                FormProviderConnection.provider == provider,
                FormProviderConnection.account_ref == account.account_ref,
            )
        )
    ).scalar_one_or_none()

    row = existing or FormProviderConnection(
        institution_id=institution_id,
        provider=provider,
        account_ref=account.account_ref,
        created_by_user_id=user_id,
    )
    row.account_name = account.account_name or row.account_name
    row.access_token = account.access_token
    if account.refresh_token:
        row.refresh_token = account.refresh_token
    row.token_expires_at = account.token_expires_at
    row.granted_scopes = account.granted_scopes
    row.status = FormConnectionStatus.ACTIVE.value
    # Reconnecting an account that was disconnected revives the same row, so
    # its forms, field maps and submission history come back with it rather
    # than being stranded behind a second connection for the same account.
    row.disconnected_at = None
    row.last_error = None
    if existing is None:
        session.add(row)
    await session.flush()
    return row


def account_from_connection(connection: FormProviderConnection) -> ProviderAccount:
    """The provider-facing view of a stored connection."""
    token = connection.access_token
    if not token:
        raise FormProviderError(
            "This connection has no stored authorisation. Reconnect the account.",
            reauth_required=True,
        )
    return ProviderAccount(
        account_ref=connection.account_ref,
        account_name=connection.account_name,
        access_token=token,
        token_expires_at=connection.token_expires_at,
        refresh_token=connection.refresh_token,
        granted_scopes=connection.granted_scopes,
    )


def mark_connection_failure(
    connection: FormProviderConnection, error: FormProviderError
) -> None:
    """Record why a provider call failed, and whether a person has to act.

    A connection that needs reauthorising is a different state from one that hit
    a bad minute at the provider, and the settings screen says so — otherwise
    every transient error reads as "reconnect your account".
    """
    connection.last_error = str(error)[:500]
    if error.reauth_required:
        connection.status = FormConnectionStatus.NEEDS_REAUTH.value


def client_for(provider: str):
    """The adapter for a provider."""
    if provider == FormProvider.META.value:
        return meta_provider.MetaFormClient()
    if provider == FormProvider.TYPEFORM.value:
        return typeform_provider.TypeformClient()
    raise FormProviderError(f"Unknown provider: {provider}")
