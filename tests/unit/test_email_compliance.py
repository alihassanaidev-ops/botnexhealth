"""Unit tests for Plan 05 email compliance: unsubscribe + bounce/complaint."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.services.email_unsubscribe import (
    make_unsubscribe_token,
    unsubscribe_footer,
    unsubscribe_url,
    verify_unsubscribe_token,
)


# ── token ────────────────────────────────────────────────────────────────────


def test_token_round_trip():
    tok = make_unsubscribe_token("inst-1", "ehash123")
    assert verify_unsubscribe_token(tok) == ("inst-1", "ehash123")


def test_token_tamper_rejected():
    tok = make_unsubscribe_token("inst-1", "ehash123")
    assert verify_unsubscribe_token(tok + "x") is None
    # swap the email_hash but keep the old signature → invalid
    inst, eh, sig = tok.split(".")
    assert verify_unsubscribe_token(f"{inst}.OTHER.{sig}") is None


def test_token_malformed_rejected():
    assert verify_unsubscribe_token(None) is None
    assert verify_unsubscribe_token("") is None
    assert verify_unsubscribe_token("only.two") is None


def test_footer_and_url():
    url = unsubscribe_url("https://app.example.com/", "tok")
    assert url == "https://app.example.com/api/email/unsubscribe?token=tok"
    footer = unsubscribe_footer(url, "Bright Smiles")
    assert "unsubscribe" in footer.lower()
    assert url in footer
    assert "Bright Smiles" in footer


# ── unsubscribe route ────────────────────────────────────────────────────────


def test_unsubscribe_valid_token_enqueues_suppress():
    from src.app.api.routes import email_compliance as mod

    tok = make_unsubscribe_token("inst-1", "ehash123")
    with patch.object(mod, "_enqueue_suppress") as enq:
        resp = asyncio.run(mod.unsubscribe(token=tok))
    enq.assert_called_once_with("inst-1", "ehash123", reason="unsubscribe")
    assert "unsubscribed" in resp.body.decode().lower()


def test_unsubscribe_invalid_token_400():
    from src.app.api.routes import email_compliance as mod

    with patch.object(mod, "_enqueue_suppress") as enq:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(mod.unsubscribe(token="bogus.token.here"))
    assert exc.value.status_code == 400
    enq.assert_not_called()


# ── resend webhook ───────────────────────────────────────────────────────────


def _req(body: bytes, headers: dict | None = None):
    r = MagicMock()
    r.body = AsyncMock(return_value=body)
    import json as _json
    r.json = AsyncMock(return_value=_json.loads(body))
    r.headers = headers or {}
    return r


def test_webhook_bounce_suppresses():
    from src.app.api.routes import email_compliance as mod

    body = (
        b'{"type":"email.bounced","data":{"to":["a@example.com"],'
        b'"tags":{"institution_id":"inst-9"}}}'
    )
    with patch("src.app.api.routes.email_compliance.settings") as ms, \
         patch.object(mod, "_enqueue_suppress") as enq:
        ms.resend_webhook_secret = None
        ms.is_production = False
        out = asyncio.run(mod.resend_webhook(_req(body)))
    assert out["status"] == "processed" and out["suppressed"] == 1
    assert enq.call_args.args[0] == "inst-9"


def test_webhook_complaint_suppresses():
    from src.app.api.routes import email_compliance as mod

    body = b'{"type":"email.complained","data":{"to":["a@example.com"],"institution_id":"inst-9"}}'
    with patch("src.app.api.routes.email_compliance.settings") as ms, \
         patch.object(mod, "_enqueue_suppress") as enq:
        ms.resend_webhook_secret = None
        ms.is_production = False
        out = asyncio.run(mod.resend_webhook(_req(body)))
    assert out["suppressed"] == 1


def test_webhook_ignores_non_suppress_event():
    from src.app.api.routes import email_compliance as mod

    body = b'{"type":"email.delivered","data":{"to":["a@example.com"]}}'
    with patch("src.app.api.routes.email_compliance.settings") as ms, \
         patch.object(mod, "_enqueue_suppress") as enq:
        ms.resend_webhook_secret = None
        ms.is_production = False
        out = asyncio.run(mod.resend_webhook(_req(body)))
    assert out["status"] == "ignored"
    enq.assert_not_called()


def test_webhook_missing_institution_scope_skips():
    from src.app.api.routes import email_compliance as mod

    body = b'{"type":"email.bounced","data":{"to":["a@example.com"]}}'
    with patch("src.app.api.routes.email_compliance.settings") as ms, \
         patch.object(mod, "_enqueue_suppress") as enq:
        ms.resend_webhook_secret = None
        ms.is_production = False
        out = asyncio.run(mod.resend_webhook(_req(body)))
    assert out["suppressed"] == 0
    enq.assert_not_called()


def test_webhook_prod_requires_secret():
    from src.app.api.routes import email_compliance as mod

    body = b'{"type":"email.bounced","data":{}}'
    with patch("src.app.api.routes.email_compliance.settings") as ms:
        ms.resend_webhook_secret = None
        ms.is_production = True
        with pytest.raises(HTTPException) as exc:
            asyncio.run(mod.resend_webhook(_req(body)))
    assert exc.value.status_code == 403


# ── gate: revoked email consent blocks even transactional (unsubscribe loop) ──


def test_revoked_email_consent_blocks_transactional():
    """Locks the unsubscribe→gate loop: a REVOKED email record beats implied
    transactional consent."""
    from src.app.models.sms_consent import ConsentStatus
    from src.app.services.automation.compliance_gate_service import ComplianceGateService

    revoked = SimpleNamespace(status=ConsentStatus.REVOKED.value, basis=None)
    result = ComplianceGateService._resolve_consent(revoked, "email", None)
    assert result.action == "block"
    assert result.reason == "email_consent_revoked"
