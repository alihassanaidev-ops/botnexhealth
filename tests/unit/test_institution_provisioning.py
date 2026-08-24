"""Unit tests for Plan 10 — per-institution provisioning."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.api.routes.admin_institutions import (
    InstitutionTwilioPhoneNumberResponse,
    _configure_location_twilio_webhook,
    _fetch_institution_twilio_phone_numbers,
    _mask_sid,
    list_institution_twilio_phone_numbers,
    reconnect_location_twilio_webhook,
)
from fastapi import HTTPException
from src.app.config import settings
from src.app.services.twilio_webhook_configuration import (
    TwilioWebhookConfigurationResult,
)


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


def test_fetch_institution_twilio_phone_numbers_uses_supplied_account():
    number = MagicMock(
        sid="PN123",
        phone_number="+15551234567",
        friendly_name="Main SMS",
        capabilities={"voice": True, "sms": True, "mms": False},
    )

    with patch("twilio.rest.Client") as mock_client:
        mock_client.return_value.incoming_phone_numbers.list.return_value = [number]

        result = _fetch_institution_twilio_phone_numbers("ACinstitution", "secret")

    mock_client.assert_called_once_with("ACinstitution", "secret")
    assert [item.model_dump() for item in result] == [
        {
            "sid": "PN123",
            "phone_number": "+15551234567",
            "friendly_name": "Main SMS",
            "capabilities": {"voice": True, "sms": True, "mms": False},
            "status": "active",
        }
    ]


@pytest.mark.asyncio
async def test_institution_phone_number_route_requires_institution_credentials():
    institution = SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        twilio_account_sid=None,
        twilio_auth_token=None,
    )

    with (
        patch("src.app.api.routes.admin_institutions.get_db_session") as mock_get_db,
        patch(
            "src.app.api.routes.admin_institutions.InstitutionService"
        ) as mock_service_class,
    ):
        mock_get_db.return_value.__aenter__.return_value = AsyncMock()
        mock_service_class.return_value.get_by_slug = AsyncMock(
            return_value=institution
        )
        with pytest.raises(HTTPException) as exc_info:
            await list_institution_twilio_phone_numbers("clinic", MagicMock())

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail == "Configure this institution's Twilio credentials first"
    )


@pytest.mark.asyncio
async def test_institution_phone_number_route_uses_institution_credentials():
    institution = SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        twilio_account_sid="ACinstitution",
        twilio_auth_token="institution-secret",
    )
    phone_numbers = [
        InstitutionTwilioPhoneNumberResponse(
            sid="PN123",
            phone_number="+15551234567",
            friendly_name="Main SMS",
            capabilities={"voice": True, "sms": True, "mms": False},
            status="active",
        )
    ]

    with (
        patch("src.app.api.routes.admin_institutions.get_db_session") as mock_get_db,
        patch(
            "src.app.api.routes.admin_institutions.InstitutionService"
        ) as mock_service_class,
        patch(
            "src.app.api.routes.admin_institutions.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=phone_numbers,
        ) as mock_to_thread,
    ):
        mock_get_db.return_value.__aenter__.return_value = AsyncMock()
        mock_service_class.return_value.get_by_slug = AsyncMock(
            return_value=institution
        )
        result = await list_institution_twilio_phone_numbers("clinic", MagicMock())

    assert result[0].phone_number == "+15551234567"
    mock_to_thread.assert_awaited_once_with(
        _fetch_institution_twilio_phone_numbers,
        "ACinstitution",
        "institution-secret",
    )


@pytest.mark.asyncio
async def test_location_number_assignment_configures_derived_inbound_webhook(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "public_api_url", "https://api.example.com")
    institution = SimpleNamespace(
        twilio_account_sid="ACinstitution",
        twilio_auth_token="institution-secret",
    )

    with patch(
        "src.app.api.routes.admin_institutions.asyncio.to_thread",
        new_callable=AsyncMock,
    ) as mock_to_thread:
        await _configure_location_twilio_webhook(institution, "+15551234567")

    mock_to_thread.assert_awaited_once()
    assert mock_to_thread.await_args.kwargs == {
        "account_sid": "ACinstitution",
        "auth_token": "institution-secret",
        "phone_number": "+15551234567",
        "webhook_url": ("https://api.example.com/api/v1/twilio/webhooks/inbound-sms"),
    }


@pytest.mark.asyncio
async def test_location_number_assignment_requires_public_api_url(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "public_api_url", None)
    institution = SimpleNamespace(
        twilio_account_sid="ACinstitution",
        twilio_auth_token="institution-secret",
    )

    with pytest.raises(HTTPException) as exc_info:
        await _configure_location_twilio_webhook(institution, "+15551234567")

    assert exc_info.value.status_code == 503
    assert "PUBLIC_API_URL" in exc_info.value.detail


@pytest.mark.asyncio
async def test_reconnect_location_twilio_webhook_uses_assigned_number():
    institution = SimpleNamespace(id="inst-1")
    location = SimpleNamespace(twilio_from_number="+15551234567")
    configured = TwilioWebhookConfigurationResult(
        phone_number_sid="PN123",
        webhook_url="https://api.example.com/api/v1/twilio/webhooks/inbound-sms",
        changed=True,
    )

    with (
        patch("src.app.api.routes.admin_institutions.get_db_session") as mock_get_db,
        patch(
            "src.app.api.routes.admin_institutions.InstitutionService"
        ) as mock_service_class,
        patch(
            "src.app.api.routes.admin_institutions._configure_location_twilio_webhook",
            new_callable=AsyncMock,
            return_value=configured,
        ) as mock_configure,
    ):
        mock_get_db.return_value.__aenter__.return_value = AsyncMock()
        service = mock_service_class.return_value
        service.get_by_slug = AsyncMock(return_value=institution)
        service.get_location_by_slug = AsyncMock(return_value=location)

        route = reconnect_location_twilio_webhook.__wrapped__
        response = await route(MagicMock(), "clinic", "main", MagicMock())

    mock_configure.assert_awaited_once_with(institution, "+15551234567")
    assert response.status == "configured"
    assert response.phone_number == "+15551234567"
    assert response.changed is True


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
    svc, _ = _make_sms_service_with_institution(
        account_sid="ACinst123", auth_token="inst_token"
    )

    with patch("src.app.services.sms_service.Client") as MockClient:
        MockClient.return_value = MagicMock()
        svc._get_twilio_client(
            account_sid="ACinst123",
            auth_token="inst_token",
        )
        MockClient.assert_called_once_with("ACinst123", "inst_token")


def test_sms_service_falls_back_to_platform_creds():
    """When no institution creds, platform creds from settings are used."""
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
    svc, _ = _make_sms_service_with_institution()

    with patch("src.app.services.sms_service.settings") as mock_settings:
        mock_settings.twillio_sid = None
        mock_settings.twillio_api_secret = None
        with pytest.raises(RuntimeError, match="Twilio credentials not configured"):
            svc._get_twilio_client(account_sid=None, auth_token=None)
