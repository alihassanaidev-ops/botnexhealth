"""Security headers on the real app, including the new Content-Security-Policy.

HIPAA audit M-7: the API and the dashboard carried no CSP, so a single
injected script could read on-screen PHI and the in-memory access token.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch) -> TestClient:
    from src.app.config import settings
    from src.app.main import app as real_app

    # The real lifespan opens the database; these checks only need headers.
    monkeypatch.setattr(settings, "database_url", None, raising=False)
    return TestClient(real_app)


def test_api_responses_carry_a_locked_down_csp(monkeypatch):
    with _client(monkeypatch) as c:
        resp = c.get("/livez")
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_interactive_docs_keep_their_own_policy(monkeypatch):
    """Swagger UI loads CDN assets, so it must not get default-src 'none'."""
    with _client(monkeypatch) as c:
        resp = c.get("/openapi.json")
    assert "content-security-policy" not in resp.headers
