"""Twilio webhook signature verification tests.

Mirrors the pattern in tests/unit/test_retell_security.py for the symmetric
case: valid sig → 200; tampered, missing, or absent-secret → fail closed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from twilio.request_validator import RequestValidator

from src.app.config import settings


_AUTH_TOKEN = "test-twilio-auth-token"
_INBOUND_URL = "http://test/api/v1/twilio/webhooks/inbound-sms"
_STATUS_URL = "http://test/api/v1/twilio/webhooks/sms-status"


def _sign(url: str, form: dict[str, str], token: str = _AUTH_TOKEN) -> str:
    return RequestValidator(token).compute_signature(url, form)


@pytest.fixture
def twilio_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "twillio_api_secret", _AUTH_TOKEN)
    return _AUTH_TOKEN


@pytest.mark.asyncio
async def test_inbound_sms_rejects_missing_signature(
    async_client: AsyncClient, twilio_token: str
):
    form = {"From": "+15555550100", "To": "+15555550199", "Body": "STOP"}

    response = await async_client.post(_INBOUND_URL, data=form)

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Twilio signature"


@pytest.mark.asyncio
async def test_inbound_sms_rejects_invalid_signature(
    async_client: AsyncClient, twilio_token: str
):
    form = {"From": "+15555550100", "To": "+15555550199", "Body": "STOP"}

    response = await async_client.post(
        _INBOUND_URL,
        data=form,
        headers={"X-Twilio-Signature": "obviously-not-a-real-signature"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Twilio signature"


@pytest.mark.asyncio
async def test_inbound_sms_rejects_signature_signed_with_wrong_token(
    async_client: AsyncClient, twilio_token: str
):
    """Signature computed with a different token must be rejected."""
    form = {"From": "+15555550100", "To": "+15555550199", "Body": "STOP"}
    bad_signature = _sign(_INBOUND_URL, form, token="some-other-token")

    response = await async_client.post(
        _INBOUND_URL,
        data=form,
        headers={"X-Twilio-Signature": bad_signature},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_inbound_sms_rejects_signature_for_tampered_body(
    async_client: AsyncClient, twilio_token: str
):
    """Re-using a valid signature with a modified payload must fail."""
    original = {"From": "+15555550100", "To": "+15555550199", "Body": "STOP"}
    signature = _sign(_INBOUND_URL, original)

    tampered = {**original, "Body": "START"}
    response = await async_client.post(
        _INBOUND_URL,
        data=tampered,
        headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_inbound_sms_returns_503_when_token_not_configured(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """If the auth token is missing, fail closed — never accept the request."""
    monkeypatch.setattr(settings, "twillio_api_secret", None)
    form = {"From": "+15555550100", "To": "+15555550199", "Body": "STOP"}

    response = await async_client.post(
        _INBOUND_URL,
        data=form,
        headers={"X-Twilio-Signature": _sign(_INBOUND_URL, form)},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Twilio auth token is not configured"


@pytest.mark.asyncio
async def test_inbound_sms_accepts_valid_signature(
    async_client: AsyncClient, twilio_token: str
):
    """A signature computed with the configured token must pass and reach the handler."""
    form = {"From": "+15555550100", "To": "+15555550199", "Body": "STOP"}
    signature = _sign(_INBOUND_URL, form)

    mock_session = AsyncMock()
    location_query = MagicMock()
    location_query.scalars.return_value.first.return_value = None
    mock_session.execute.return_value = location_query

    with patch(
        "src.app.api.routes.twilio_webhooks.get_system_db_session"
    ) as mock_get_db, patch(
        "src.app.api.routes.twilio_webhooks.capture_dead_letter",
        new=AsyncMock(),
    ):
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            _INBOUND_URL,
            data=form,
            headers={"X-Twilio-Signature": signature},
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_sms_status_rejects_missing_signature(
    async_client: AsyncClient, twilio_token: str
):
    response = await async_client.post(
        _STATUS_URL,
        data={"MessageSid": "SM123", "MessageStatus": "delivered"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_sms_status_rejects_invalid_signature(
    async_client: AsyncClient, twilio_token: str
):
    response = await async_client.post(
        _STATUS_URL,
        data={"MessageSid": "SM123", "MessageStatus": "delivered"},
        headers={"X-Twilio-Signature": "bogus"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_sms_status_accepts_valid_signature(
    async_client: AsyncClient, twilio_token: str
):
    form = {"MessageSid": "SM123", "MessageStatus": "delivered"}
    signature = _sign(_STATUS_URL, form)

    mock_session = AsyncMock()

    sms_service = MagicMock()
    sms_service.update_delivery_status = AsyncMock(return_value=MagicMock())

    with patch(
        "src.app.api.routes.twilio_webhooks.get_system_db_session"
    ) as mock_get_db, patch(
        "src.app.api.routes.twilio_webhooks.SmsService", return_value=sms_service
    ):
        mock_get_db.return_value.__aenter__.return_value = mock_session

        response = await async_client.post(
            _STATUS_URL,
            data=form,
            headers={"X-Twilio-Signature": signature},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "updated"}
    sms_service.update_delivery_status.assert_awaited_once()
