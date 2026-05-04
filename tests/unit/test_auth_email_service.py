from __future__ import annotations

import logging

import httpx
import pytest

from src.app.config import settings
from src.app.services.auth_email_service import AuthEmailService


def test_resolve_redirect_url_allows_relative_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_frontend_base_url", "https://dashboard.example.com")
    monkeypatch.setattr(settings, "auth_redirect_allowed_hosts", "")
    monkeypatch.setattr(settings, "app_env", "test")

    service = AuthEmailService()

    resolved = service.resolve_redirect_url(
        redirect_url="/set-password",
        default_path="/set-password",
    )

    assert resolved == "https://dashboard.example.com/set-password"


def test_resolve_redirect_url_rejects_unapproved_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_frontend_base_url", "https://dashboard.example.com")
    monkeypatch.setattr(settings, "auth_redirect_allowed_hosts", "")
    monkeypatch.setattr(settings, "app_env", "test")

    service = AuthEmailService()

    with pytest.raises(ValueError, match="Redirect URL host is not allowed"):
        service.resolve_redirect_url(
            redirect_url="https://evil.example.com/set-password",
            default_path="/set-password",
        )


def test_resolve_redirect_url_rejects_http_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_frontend_base_url", "https://dashboard.example.com")
    monkeypatch.setattr(settings, "auth_redirect_allowed_hosts", "dashboard.example.com")
    monkeypatch.setattr(settings, "app_env", "production")

    service = AuthEmailService()

    with pytest.raises(ValueError, match="Redirect URL must use https"):
        service.resolve_redirect_url(
            redirect_url="http://dashboard.example.com/set-password",
            default_path="/set-password",
        )


@pytest.mark.asyncio
async def test_failed_email_send_does_not_log_response_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider error responses must never reach the log: the request body
    contains a live ?token=... URL, and any echo (provider, proxy, WAF)
    would log the token — which is effectively a credential."""
    monkeypatch.setattr(settings, "auth_frontend_base_url", "https://dashboard.example.com")
    monkeypatch.setattr(settings, "auth_redirect_allowed_hosts", "dashboard.example.com")
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "resend_api_key", "test-api-key")
    monkeypatch.setattr(settings, "resend_from_email", "no-reply@example.com")
    monkeypatch.setattr(settings, "resend_reply_to", "")

    secret_token = "RESET_TOKEN_SHOULD_NOT_BE_IN_LOGS"

    def echo_handler(request: httpx.Request) -> httpx.Response:
        # Simulate a provider/proxy that echoes the submitted payload back
        # in the error body.
        return httpx.Response(
            status_code=400,
            content=request.content,
            headers={"x-request-id": "resend-req-abc123"},
        )

    transport = httpx.MockTransport(echo_handler)

    original_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "src.app.services.auth_email_service.httpx.AsyncClient",
        fake_async_client,
    )

    service = AuthEmailService()

    with caplog.at_level(logging.ERROR, logger="src.app.services.auth_email_service"):
        with pytest.raises(httpx.HTTPStatusError):
            await service.send_password_reset_email(
                email="user@example.com",
                token=secret_token,
            )

    captured_log = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_token not in captured_log, (
        f"Reset token leaked into logs: {captured_log!r}"
    )
    assert "token=" not in captured_log, (
        "Any 'token=' substring (URL query) must not appear in error logs"
    )
    # The redaction-safe metadata still appears for triage.
    assert "provider=resend" in captured_log
    assert "status=400" in captured_log
    assert "request_id=resend-req-abc123" in captured_log
