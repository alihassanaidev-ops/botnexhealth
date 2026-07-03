"""Unit tests for Plan 10 — per-institution provisioning."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.institution import Institution
from src.app.api.routes.admin_institutions import _mask_sid


# ---------------------------------------------------------------------------
# Institution model — encrypted Twilio credential round-trips
# Test via the module-level encrypt_value / decrypt_value functions directly,
# since instantiating an ORM model outside a session is fragile.
# ---------------------------------------------------------------------------


def test_twilio_account_sid_round_trip():
    """encrypt_value + decrypt_value round-trip for a Twilio SID."""
    from src.app.models.institution import encrypt_value, decrypt_value
    raw = "ACtest1234567890abcdef"
    encrypted = encrypt_value(raw)
    assert encrypted is not None
    assert encrypted != raw
    assert decrypt_value(encrypted) == raw


def test_twilio_auth_token_round_trip():
    from src.app.models.institution import encrypt_value, decrypt_value
    raw = "secret_token_abc"
    encrypted = encrypt_value(raw)
    assert encrypted != raw
    assert decrypt_value(encrypted) == raw


def test_twilio_creds_none_when_not_set():
    from src.app.models.institution import encrypt_value, decrypt_value
    assert encrypt_value(None) is None
    assert decrypt_value(None) is None


# ---------------------------------------------------------------------------
# _mask_sid helper
# ---------------------------------------------------------------------------


def test_mask_sid_normal():
    assert _mask_sid("AC1234567890abcdef") == "AC12****cdef"


def test_mask_sid_none():
    assert _mask_sid(None) is None


def test_mask_sid_short():
    assert _mask_sid("AC12") == "AC12"


# ---------------------------------------------------------------------------
# SmsService — Twilio client credential selection
# ---------------------------------------------------------------------------


def _make_sms_service_with_institution(account_sid=None, auth_token=None):
    """Build a SmsService instance with a mocked session and institution."""
    from src.app.services.sms_service import SmsService

    session = AsyncMock()
    svc = SmsService(session)

    institution = MagicMock()
    institution.twilio_account_sid = account_sid
    institution.twilio_auth_token = auth_token
    return svc, institution


def test_sms_service_uses_institution_creds_when_set():
    """When institution has sub-account creds, they are passed to the Twilio client."""
    from src.app.services.sms_service import SmsService

    svc, _ = _make_sms_service_with_institution(
        account_sid="ACinst123", auth_token="inst_token"
    )

    with patch("src.app.services.sms_service.Client") as MockClient:
        MockClient.return_value = MagicMock()
        client = svc._get_twilio_client(
            account_sid="ACinst123",
            auth_token="inst_token",
        )
        MockClient.assert_called_once_with("ACinst123", "inst_token")


def test_sms_service_falls_back_to_platform_creds():
    """When no institution creds, platform creds from settings are used."""
    from src.app.services.sms_service import SmsService

    svc, _ = _make_sms_service_with_institution(account_sid=None, auth_token=None)

    with (
        patch("src.app.services.sms_service.Client") as MockClient,
        patch("src.app.services.sms_service.settings") as mock_settings,
    ):
        mock_settings.twillio_sid = "ACplatform"
        mock_settings.twillio_api_secret = "platform_secret"
        MockClient.return_value = MagicMock()
        svc._get_twilio_client(account_sid=None, auth_token=None)
        MockClient.assert_called_once_with("ACplatform", "platform_secret")


def test_sms_service_raises_when_no_creds_at_all():
    """RuntimeError raised when neither institution nor platform creds are set."""
    from src.app.services.sms_service import SmsService

    svc, _ = _make_sms_service_with_institution()

    with patch("src.app.services.sms_service.settings") as mock_settings:
        mock_settings.twillio_sid = None
        mock_settings.twillio_api_secret = None
        with pytest.raises(RuntimeError, match="Twilio credentials not configured"):
            svc._get_twilio_client(account_sid=None, auth_token=None)
