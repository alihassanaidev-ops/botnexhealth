from __future__ import annotations

import json
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


def _capture_transport(captured: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code=200, json={"id": "sent"})

    return httpx.MockTransport(handler)


def _patch_resend(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> None:
    monkeypatch.setattr(settings, "resend_api_key", "test-api-key")
    monkeypatch.setattr(settings, "resend_from_email", "no-reply@example.com")
    monkeypatch.setattr(settings, "resend_reply_to", "")
    original_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(
        "src.app.services.auth_email_service.httpx.AsyncClient", fake_async_client
    )


@pytest.mark.asyncio
async def test_login_code_email_carries_the_code_and_no_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sign-in code mail must contain the code and no clickable URL.

    A code email that also carries a link is a ready-made phishing
    template, and the code is only usable in the browser tab that already
    holds the login ticket — so a link would buy nothing.
    """
    captured: list[httpx.Request] = []
    _patch_resend(monkeypatch, _capture_transport(captured))

    await AuthEmailService().send_login_code_email(
        email="user@example.com", code="481902", ttl_minutes=10
    )

    assert len(captured) == 1
    payload = json.loads(captured[0].content)
    assert payload["to"] == ["user@example.com"]
    assert payload["subject"] == "Your ScaleNexus sign-in code"
    assert "481902" in payload["html"]
    assert "481902" in payload["text"]
    assert "expires in 10 minutes" in payload["text"]
    assert "http://" not in payload["html"] and "https://" not in payload["html"].replace(
        'xmlns="http://www.w3.org/1999/xhtml"', ""
    )


@pytest.mark.asyncio
async def test_login_code_email_pluralises_a_one_minute_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx.Request] = []
    _patch_resend(monkeypatch, _capture_transport(captured))

    await AuthEmailService().send_login_code_email(
        email="user@example.com", code="000123", ttl_minutes=1
    )

    assert "expires in 1 minute " in json.loads(captured[0].content)["text"]


@pytest.mark.asyncio
async def test_failed_login_code_send_does_not_log_the_code(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Same discipline as the reset mail: the code is a live credential."""
    code = "918273"

    def echo_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=500,
            content=request.content,
            headers={"x-request-id": "resend-req-xyz"},
        )

    _patch_resend(monkeypatch, httpx.MockTransport(echo_handler))

    with caplog.at_level(logging.ERROR, logger="src.app.services.auth_email_service"):
        with pytest.raises(httpx.HTTPStatusError):
            await AuthEmailService().send_login_code_email(
                email="user@example.com", code=code, ttl_minutes=10
            )

    captured_log = "\n".join(record.getMessage() for record in caplog.records)
    assert code not in captured_log
    assert "provider=resend" in captured_log
