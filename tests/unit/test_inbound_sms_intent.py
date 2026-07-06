"""STOP/HELP/START detection must catch the keyword anywhere in the body."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.api.routes.twilio_webhooks import (
    _classify_confirmation_reply,
    _classify_intent,
    _verified_form,
)


@pytest.mark.parametrize(
    "body,expected",
    [
        ("STOP", "STOP"),
        ("stop", "STOP"),
        ("STOP!", "STOP"),
        ("Please STOP calling me", "STOP"),
        ("please stop", "STOP"),
        ("UNSUBSCRIBE", "STOP"),
        ("cancel my notifications", "STOP"),
        ("END", "STOP"),
        ("START", "START"),
        ("Yes, START please", "START"),
        ("HELP", "HELP"),
        ("more info please", "HELP"),
        # STOP wins over START in the unlikely "STOP and START" case.
        ("STOP and START", "STOP"),
        # French / CASL opt-out keywords (accented and un-accented forms).
        ("ARRET", "STOP"),
        ("ARRÊT", "STOP"),
        ("Arrêt", "STOP"),
        ("arrêt s'il vous plaît", "STOP"),
        ("DÉSABONNER", "STOP"),
        ("DESABONNER", "STOP"),
        ("retirer", "STOP"),
        ("AIDE", "HELP"),
        ("aide", "HELP"),
        # No keyword token → empty.
        ("", ""),
        ("Thanks!", ""),
        ("STOPPING by tomorrow", ""),  # not a whole-word STOP
        ("CANCELLATION confirmed", ""),  # not a whole-word CANCEL
    ],
)
def test_classify_intent_finds_keywords_anywhere(body: str, expected: str) -> None:
    assert _classify_intent(body) == expected


@pytest.mark.parametrize("body", ["YES", "yes", "Y", "confirm", "C", "1", "1."])
def test_classify_confirmation_reply_accepts_bare_confirm_tokens(body: str) -> None:
    assert _classify_confirmation_reply(body) is True


@pytest.mark.parametrize(
    "body",
    ["", "yes but reschedule", "confirm and cancel", "oui", "cancel", "STOP", "11"],
)
def test_classify_confirmation_reply_rejects_ambiguous_or_non_confirm_tokens(body: str) -> None:
    assert _classify_confirmation_reply(body) is False


# ---------------------------------------------------------------------------
# Webhook signature validation — sub-account auth token (Plan 10)
# ---------------------------------------------------------------------------


def _make_request(fields: dict, signature="sig"):
    form_data = MagicMock()
    form_data.multi_items = MagicMock(return_value=list(fields.items()))
    request = MagicMock()
    request.form = AsyncMock(return_value=form_data)
    request.headers = {"X-Twilio-Signature": signature}
    request.url = "https://api.example.com/twilio/webhooks/inbound-sms"
    return request


def _session_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def test_verified_form_validates_with_subaccount_token():
    """The signature is validated with the destination number's sub-account
    token, not the platform token."""
    request = _make_request({"To": "+16475550001", "From": "+14165551234", "Body": "hi"})
    captured = {}

    def _make_validator(token):
        captured["token"] = token
        v = MagicMock()
        v.validate = MagicMock(return_value=True)
        return v

    resolver = MagicMock()
    resolver.resolve_auth_token = AsyncMock(return_value="tok_subaccount")

    with (
        patch("src.app.api.routes.twilio_webhooks.settings") as s,
        patch(
            "src.app.api.routes.twilio_webhooks.get_system_db_session",
            return_value=_session_cm(AsyncMock()),
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.TenantTwilioCredentialResolver",
            return_value=resolver,
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.RequestValidator",
            side_effect=_make_validator,
        ),
    ):
        s.twillio_api_secret = "tok_platform"
        form = asyncio.run(_verified_form(request))

    assert form["To"] == "+16475550001"
    assert captured["token"] == "tok_subaccount"
    resolver.resolve_auth_token.assert_awaited_once_with("+16475550001", "+14165551234")


def test_verified_form_falls_back_to_platform_token():
    """When the number belongs to no sub-account the resolver returns the
    platform token and validation still succeeds."""
    request = _make_request({"To": "+16475550001", "From": "+14165551234", "Body": "hi"})
    captured = {}

    def _make_validator(token):
        captured["token"] = token
        v = MagicMock()
        v.validate = MagicMock(return_value=True)
        return v

    resolver = MagicMock()
    resolver.resolve_auth_token = AsyncMock(return_value="tok_platform")

    with (
        patch("src.app.api.routes.twilio_webhooks.settings") as s,
        patch(
            "src.app.api.routes.twilio_webhooks.get_system_db_session",
            return_value=_session_cm(AsyncMock()),
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.TenantTwilioCredentialResolver",
            return_value=resolver,
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.RequestValidator",
            side_effect=_make_validator,
        ),
    ):
        s.twillio_api_secret = "tok_platform"
        asyncio.run(_verified_form(request))

    assert captured["token"] == "tok_platform"


def test_verified_form_rejects_bad_signature():
    from fastapi import HTTPException

    request = _make_request({"To": "+16475550001", "From": "+14165551234"})
    resolver = MagicMock()
    resolver.resolve_auth_token = AsyncMock(return_value="tok_platform")

    def _make_validator(token):
        v = MagicMock()
        v.validate = MagicMock(return_value=False)
        return v

    with (
        patch("src.app.api.routes.twilio_webhooks.settings") as s,
        patch(
            "src.app.api.routes.twilio_webhooks.get_system_db_session",
            return_value=_session_cm(AsyncMock()),
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.TenantTwilioCredentialResolver",
            return_value=resolver,
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.RequestValidator",
            side_effect=_make_validator,
        ),
    ):
        s.twillio_api_secret = "tok_platform"
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(_verified_form(request))

    assert exc_info.value.status_code == 401
