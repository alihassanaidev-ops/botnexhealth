"""The OAuth state parameter, which travels through a browser and comes back.

It is the one piece of this feature an attacker holds directly. If it could be
forged or replayed, a callback could attach somebody else's Facebook Page to a
clinic's account, or attach a clinic's Page to an account they do not own.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.app.services.forms.connection_service import (
    STATE_TTL_SECONDS,
    decode_state,
    encode_state,
    oauth_redirect_uri,
)
from src.app.services.forms.providers.base import FormProviderError


def _state(**overrides) -> str:
    return encode_state(
        provider=overrides.get("provider", "typeform"),
        institution_id=overrides.get("institution_id", "inst-1"),
        user_id=overrides.get("user_id", "user-1"),
        now=overrides.get("now"),
    )


def test_round_trip_carries_the_tenant_and_provider() -> None:
    decoded = decode_state(_state())
    assert decoded.provider == "typeform"
    assert decoded.institution_id == "inst-1"
    assert decoded.user_id == "user-1"


def test_a_tampered_payload_is_rejected() -> None:
    body, signature = _state().split(".", 1)
    # Same signature, different body: exactly the substitution a cross-tenant
    # replay would attempt.
    forged = f"{body[:-2]}xy.{signature}"
    with pytest.raises(FormProviderError):
        decode_state(forged)


def test_a_stripped_signature_is_rejected() -> None:
    body = _state().split(".", 1)[0]
    with pytest.raises(FormProviderError):
        decode_state(body)


def test_an_expired_state_is_rejected() -> None:
    issued = datetime.now(timezone.utc) - timedelta(seconds=STATE_TTL_SECONDS + 60)
    with pytest.raises(FormProviderError):
        decode_state(_state(now=issued))


def test_a_state_inside_the_window_still_works() -> None:
    issued = datetime.now(timezone.utc) - timedelta(seconds=STATE_TTL_SECONDS - 60)
    assert decode_state(_state(now=issued)).institution_id == "inst-1"


def test_an_unknown_provider_is_rejected() -> None:
    with pytest.raises(FormProviderError):
        decode_state(_state(provider="salesforce"))


def test_every_rejection_reads_the_same() -> None:
    """A reply that says *why* teaches whoever sent it how to send a better one."""
    messages = set()
    for bad in ("", "nonsense", _state()[:-4]):
        try:
            decode_state(bad)
        except FormProviderError as error:
            messages.add(str(error))
    assert len(messages) == 1


def test_both_providers_share_one_redirect_uri() -> None:
    """Only one URI has to be registered in each provider's app settings."""
    assert oauth_redirect_uri().endswith(
        "/institution-admin/form-integrations/callback"
    )
