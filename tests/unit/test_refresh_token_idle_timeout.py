"""HIPAA §164.312(a)(2)(iii): refresh-cookie TTL is the idle-timeout
window. Each /refresh resets it; a walked-away tab expires after the
configured idle window.

Pin two contracts so a future regression that re-extends the cookie
to days fails CI:

1. ``RefreshTokenService._ttl_seconds()`` returns
   ``settings.refresh_token_ttl_minutes * 60`` — the source of truth
   for both the Redis key TTL and the issued cookie's max-age.
2. The cookie ``max_age`` set on /login responses matches the same
   value (so the browser-side expiry tracks the server-side one).
"""

from __future__ import annotations

import pytest

from src.app.config import settings


def test_default_refresh_ttl_is_one_hour() -> None:
    """Default config bakes in the §164.312(a)(2)(iii) idle window."""
    assert settings.refresh_token_ttl_minutes == 60


def test_refresh_token_service_ttl_seconds_uses_minutes_not_days() -> None:
    """Source of truth: the Redis SETEX uses this. A regression that
    multiplies by 24 * 60 * 60 (days × seconds) instead of 60 (minutes ×
    seconds) would silently extend sessions to multiple-month windows."""
    from src.app.services.refresh_token_service import RefreshTokenService

    expected = settings.refresh_token_ttl_minutes * 60
    assert RefreshTokenService._ttl_seconds() == expected
    # Belt-and-braces: catch the literal "days" mistake.
    assert RefreshTokenService._ttl_seconds() < 24 * 60 * 60, (
        "Refresh TTL slipped above one day; HIPAA idle-logoff window violated"
    )


def test_refresh_token_service_ttl_scales_with_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bumping the setting up bumps the resolved TTL."""
    from src.app.services.refresh_token_service import RefreshTokenService

    monkeypatch.setattr(settings, "refresh_token_ttl_minutes", 30)
    assert RefreshTokenService._ttl_seconds() == 30 * 60

    monkeypatch.setattr(settings, "refresh_token_ttl_minutes", 240)
    assert RefreshTokenService._ttl_seconds() == 240 * 60


def test_refresh_cookie_max_age_matches_service_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cookie max-age (browser-side expiry) MUST match the Redis
    TTL (server-side expiry). If they drift, either the SPA holds a
    cookie that's already 401 against the backend, or the backend
    accepts a refresh against a session whose Redis row is gone."""
    from fastapi import Response

    from src.app.api.routes.auth import _set_refresh_cookie
    from src.app.services.refresh_token_service import RefreshTokenService

    monkeypatch.setattr(settings, "refresh_token_ttl_minutes", 90)

    response = Response()
    _set_refresh_cookie(response, "fake-token")

    set_cookie = response.headers.get("set-cookie", "")
    assert "Max-Age=5400" in set_cookie, (
        f"Cookie Max-Age should match service TTL "
        f"({RefreshTokenService._ttl_seconds()}s); got: {set_cookie!r}"
    )
