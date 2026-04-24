from __future__ import annotations

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
