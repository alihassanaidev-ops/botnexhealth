"""Production proxy configuration must preserve Twilio's signed HTTPS URL."""

from __future__ import annotations

import asyncio
import importlib

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


def test_gunicorn_trusts_configured_proxy_cidrs(monkeypatch) -> None:
    trusted_cidrs = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", trusted_cidrs)

    config = importlib.import_module("src.app.gunicorn_conf")
    config = importlib.reload(config)

    assert config.forwarded_allow_ips == trusted_cidrs


def test_trusted_alb_preserves_forwarded_https_scheme(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    config = importlib.import_module("src.app.gunicorn_conf")
    config = importlib.reload(config)
    observed: dict[str, str] = {}

    async def app(scope, receive, send) -> None:
        observed["scheme"] = scope["scheme"]

    middleware = ProxyHeadersMiddleware(
        app,
        trusted_hosts=config.forwarded_allow_ips,
    )
    scope = {
        "type": "http",
        "scheme": "http",
        "client": ("10.0.61.178", 44304),
        "server": ("api.staging.scalenexus.ai", 80),
        "headers": [(b"x-forwarded-proto", b"https")],
    }

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        return None

    asyncio.run(middleware(scope, receive, send))

    assert observed["scheme"] == "https"
