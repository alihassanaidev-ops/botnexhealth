"""Cross-origin POSTs to cookie-authenticated routes must be rejected.

This is defense-in-depth on top of SameSite=strict on the refresh cookie.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.api.routes import auth as auth_routes


def _request(headers: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        headers={k.lower(): v for k, v in headers.items()},
        client=None,
    )


@pytest.fixture
def explicit_origins(monkeypatch: pytest.MonkeyPatch):
    """Force the allowlist away from wildcard, regardless of test env settings."""
    monkeypatch.setattr(
        auth_routes,
        "_allowed_origin_set",
        lambda: frozenset({"https://app.example.com"}),
    )


def test_origin_check_allows_matching_origin(explicit_origins):
    auth_routes._enforce_same_origin(
        _request({"origin": "https://app.example.com"})
    )


def test_origin_check_falls_back_to_referer(explicit_origins):
    auth_routes._enforce_same_origin(
        _request({"referer": "https://app.example.com/some/path"})
    )


def test_origin_check_rejects_foreign_origin(explicit_origins):
    with pytest.raises(HTTPException) as exc:
        auth_routes._enforce_same_origin(
            _request({"origin": "https://evil.example.com"})
        )
    assert exc.value.status_code == 403


def test_origin_check_rejects_missing_headers(explicit_origins):
    with pytest.raises(HTTPException) as exc:
        auth_routes._enforce_same_origin(_request({}))
    assert exc.value.status_code == 403


def test_origin_check_no_op_when_allowlist_is_empty(monkeypatch):
    monkeypatch.setattr(auth_routes, "_allowed_origin_set", lambda: frozenset())
    # No allowlist (e.g. dev) → don't second-guess; cookies + SameSite carry it.
    auth_routes._enforce_same_origin(_request({}))
