"""Tests for deployment-derived Twilio webhook configuration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.app.config import Settings
from src.app.services.twilio_webhook_configuration import (
    TwilioPhoneNumberNotFoundError,
    TwilioPhoneNumberSmsUnsupportedError,
    TwilioSmsApplicationConflictError,
    configure_inbound_sms_webhook,
)


def _settings(**overrides: object) -> Settings:
    return Settings(
        jwt_secret="test-secret",
        app_env="test",
        _env_file=None,
        **overrides,
    )


def _twilio_number(**overrides: object) -> SimpleNamespace:
    values = {
        "sid": "PN123",
        "phone_number": "+15551234567",
        "capabilities": {"sms": True, "voice": True},
        "sms_url": None,
        "sms_method": None,
        "sms_application_sid": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_public_api_url_derives_both_twilio_webhooks() -> None:
    config = _settings(public_api_url=" https://api.example.com/staging/ ")

    assert config.public_api_url == "https://api.example.com/staging"
    assert config.twilio_inbound_sms_webhook_url == (
        "https://api.example.com/staging/api/v1/twilio/webhooks/inbound-sms"
    )
    assert config.effective_twilio_sms_status_callback_url == (
        "https://api.example.com/staging/api/v1/twilio/webhooks/sms-status"
    )


def test_explicit_twilio_status_callback_overrides_public_api_url() -> None:
    config = _settings(
        public_api_url="https://api.example.com",
        twilio_sms_status_callback_url="https://callbacks.example.com/twilio/status/",
    )

    assert config.effective_twilio_sms_status_callback_url == (
        "https://callbacks.example.com/twilio/status"
    )


def test_public_api_url_rejects_query_parameters() -> None:
    with pytest.raises(ValueError, match="PUBLIC_API_URL"):
        _settings(public_api_url="https://api.example.com?environment=staging")


def test_configure_inbound_sms_webhook_updates_selected_number() -> None:
    number = _twilio_number(sms_url="https://old.example.com/inbound", sms_method="GET")

    with patch("twilio.rest.Client") as mock_client_class:
        client = mock_client_class.return_value
        client.incoming_phone_numbers.list.return_value = [number]

        result = configure_inbound_sms_webhook(
            account_sid="ACinstitution",
            auth_token="secret",
            phone_number="+15551234567",
            webhook_url="https://api.example.com/api/v1/twilio/webhooks/inbound-sms",
        )

    mock_client_class.assert_called_once_with("ACinstitution", "secret")
    client.incoming_phone_numbers.list.assert_called_once_with(
        phone_number="+15551234567",
        limit=2,
    )
    client.incoming_phone_numbers.assert_called_once_with("PN123")
    client.incoming_phone_numbers.return_value.update.assert_called_once_with(
        sms_url="https://api.example.com/api/v1/twilio/webhooks/inbound-sms",
        sms_method="POST",
    )
    assert result.changed is True


def test_configure_inbound_sms_webhook_is_idempotent() -> None:
    webhook_url = "https://api.example.com/api/v1/twilio/webhooks/inbound-sms"
    number = _twilio_number(sms_url=webhook_url, sms_method="POST")

    with patch("twilio.rest.Client") as mock_client_class:
        client = mock_client_class.return_value
        client.incoming_phone_numbers.list.return_value = [number]

        result = configure_inbound_sms_webhook(
            account_sid="ACinstitution",
            auth_token="secret",
            phone_number="+15551234567",
            webhook_url=webhook_url,
        )

    client.incoming_phone_numbers.assert_not_called()
    assert result.changed is False


def test_configure_inbound_sms_webhook_rejects_foreign_number() -> None:
    with patch("twilio.rest.Client") as mock_client_class:
        mock_client_class.return_value.incoming_phone_numbers.list.return_value = []

        with pytest.raises(TwilioPhoneNumberNotFoundError):
            configure_inbound_sms_webhook(
                account_sid="ACinstitution",
                auth_token="secret",
                phone_number="+15551234567",
                webhook_url="https://api.example.com/inbound",
            )


def test_configure_inbound_sms_webhook_rejects_non_sms_number() -> None:
    number = _twilio_number(capabilities={"sms": False, "voice": True})
    with patch("twilio.rest.Client") as mock_client_class:
        mock_client_class.return_value.incoming_phone_numbers.list.return_value = [
            number
        ]

        with pytest.raises(TwilioPhoneNumberSmsUnsupportedError):
            configure_inbound_sms_webhook(
                account_sid="ACinstitution",
                auth_token="secret",
                phone_number="+15551234567",
                webhook_url="https://api.example.com/inbound",
            )


def test_configure_inbound_sms_webhook_preserves_sms_application_binding() -> None:
    number = _twilio_number(sms_application_sid="AP123")
    with patch("twilio.rest.Client") as mock_client_class:
        client = mock_client_class.return_value
        client.incoming_phone_numbers.list.return_value = [number]

        with pytest.raises(TwilioSmsApplicationConflictError):
            configure_inbound_sms_webhook(
                account_sid="ACinstitution",
                auth_token="secret",
                phone_number="+15551234567",
                webhook_url="https://api.example.com/inbound",
            )

    client.incoming_phone_numbers.assert_not_called()
