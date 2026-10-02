"""Unit tests for TenantTwilioCredentialResolver (Plan 10).

Covers institution-vs-platform fallback for SMS + email and the inbound-webhook
auth-token resolution used to validate Twilio signatures against a sub-account.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.services.messaging_credentials import TenantTwilioCredentialResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_institution(sid=None, token=None, email_from=None, email_name=None):
    inst = MagicMock()
    inst.twilio_account_sid = sid
    inst.twilio_auth_token = token
    inst.email_from_address = email_from
    inst.email_from_name = email_name
    return inst


def _make_location(from_number="+16475550001"):
    loc = MagicMock()
    loc.twilio_from_number = from_number
    return loc


# ---------------------------------------------------------------------------
# resolve_sms — institution vs platform fallback
# ---------------------------------------------------------------------------


def test_resolve_sms_uses_institution_subaccount():
    inst = _make_institution(sid="AC_sub", token="tok_sub")
    loc = _make_location(from_number="+16475550001")
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = "AC_platform"
        s.twillio_api_secret = "tok_platform"
        creds = TenantTwilioCredentialResolver.resolve_sms(inst, loc)
    assert creds.account_sid == "AC_sub"
    assert creds.auth_token == "tok_sub"
    assert creds.from_number == "+16475550001"
    assert creds.is_subaccount is True


def test_resolve_sms_falls_back_to_platform():
    inst = _make_institution(sid=None, token=None)
    loc = _make_location()
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = "AC_platform"
        s.twillio_api_secret = "tok_platform"
        creds = TenantTwilioCredentialResolver.resolve_sms(inst, loc)
    assert creds.account_sid == "AC_platform"
    assert creds.auth_token == "tok_platform"
    assert creds.is_subaccount is False


def test_resolve_sms_no_institution_uses_platform():
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = "AC_platform"
        s.twillio_api_secret = "tok_platform"
        creds = TenantTwilioCredentialResolver.resolve_sms(None, _make_location())
    assert creds.account_sid == "AC_platform"
    assert creds.is_subaccount is False


def test_resolve_sms_partial_creds_not_subaccount():
    """SID without a token is not a usable sub-account — fall back per field."""
    inst = _make_institution(sid="AC_sub", token=None)
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = "AC_platform"
        s.twillio_api_secret = "tok_platform"
        creds = TenantTwilioCredentialResolver.resolve_sms(inst, _make_location())
    assert creds.account_sid == "AC_sub"
    assert creds.auth_token == "tok_platform"
    assert creds.is_subaccount is False


def test_resolve_sms_no_from_number():
    creds = TenantTwilioCredentialResolver.resolve_sms(
        _make_institution(sid="AC", token="tok"), _make_location(from_number=None)
    )
    assert creds.from_number is None


# ---------------------------------------------------------------------------
# resolve_email_from — institution vs platform fallback
# ---------------------------------------------------------------------------


def test_resolve_email_uses_institution_address():
    inst = _make_institution(email_from="clinic@example.com", email_name="My Clinic")
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.resend_from_email = "platform@example.com"
        resolved = TenantTwilioCredentialResolver.resolve_email_from(inst)
    assert resolved.from_address == "clinic@example.com"
    assert resolved.from_name == "My Clinic"
    assert resolved.is_institution is True


def test_resolve_email_falls_back_to_platform():
    inst = _make_institution(email_from=None, email_name=None)
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.resend_from_email = "platform@example.com"
        resolved = TenantTwilioCredentialResolver.resolve_email_from(inst)
    assert resolved.from_address == "platform@example.com"
    assert resolved.is_institution is False


# ---------------------------------------------------------------------------
# resolve_auth_token — webhook signature token selection
# ---------------------------------------------------------------------------


def _resolver_with_lookup(institution):
    session = AsyncMock()
    resolver = TenantTwilioCredentialResolver(session)
    resolver._institution_for_number = AsyncMock(return_value=institution)
    return resolver


def test_resolve_auth_token_returns_subaccount_token():
    inst = _make_institution(sid="AC_sub", token="tok_sub")
    resolver = _resolver_with_lookup(inst)
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_api_secret = "tok_platform"
        token = asyncio.run(resolver.resolve_auth_token("+16475550001", None))
    assert token == "tok_sub"


def test_resolve_auth_token_falls_back_to_platform_when_no_subaccount():
    resolver = _resolver_with_lookup(None)  # no matching institution
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_api_secret = "tok_platform"
        token = asyncio.run(resolver.resolve_auth_token("+16475550001", "+14165551234"))
    assert token == "tok_platform"


def test_resolve_auth_token_no_session_uses_platform():
    resolver = TenantTwilioCredentialResolver(session=None)
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_api_secret = "tok_platform"
        token = asyncio.run(resolver.resolve_auth_token("+16475550001"))
    assert token == "tok_platform"


def test_resolve_auth_token_tries_candidates_in_order():
    """First candidate that maps to a sub-account token wins (To before From)."""
    inst = _make_institution(sid="AC_sub", token="tok_from")
    session = AsyncMock()
    resolver = TenantTwilioCredentialResolver(session)

    async def _lookup(number):
        return inst if number == "+16475550002" else None

    resolver._institution_for_number = AsyncMock(side_effect=_lookup)
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_api_secret = "tok_platform"
        # "To" has no sub-account; "From" does — resolver must find it.
        token = asyncio.run(resolver.resolve_auth_token("+16475550001", "+16475550002"))
    assert token == "tok_from"
