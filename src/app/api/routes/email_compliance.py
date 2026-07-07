"""Public email-compliance routes (Plan 05): one-click unsubscribe + Resend
bounce/complaint webhook. Both suppress EMAIL for a recipient by enqueuing a
revoked-email-consent write (the gate then blocks future email to that address).
Unauthenticated by design — the unsubscribe link comes from an email, and the
webhook is provider-to-us with its own signature.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from src.app.config import settings
from src.app.services.email_unsubscribe import verify_unsubscribe_token
from src.app.services.sms_privacy import hash_email, hash_for_logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/email", tags=["Email Compliance"])

# Resend event types that mean "stop emailing this address".
_SUPPRESS_EVENTS = frozenset({"email.bounced", "email.complained"})


def _enqueue_suppress(institution_id: str, email_hash: str, reason: str) -> None:
    from src.app.tasks.email_compliance import suppress_email_consent

    suppress_email_consent.delay(
        institution_id=institution_id, email_hash=email_hash, reason=reason
    )


@router.get("/unsubscribe", response_class=PlainTextResponse)
async def unsubscribe(token: str = Query(..., description="Signed unsubscribe token")) -> PlainTextResponse:
    """One-click unsubscribe. Verifies the signed token and suppresses email for
    the bound recipient. Returns a plain confirmation."""
    verified = verify_unsubscribe_token(token)
    if verified is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired unsubscribe link")
    institution_id, email_hash = verified
    _enqueue_suppress(institution_id, email_hash, reason="unsubscribe")
    logger.info("email unsubscribe: institution=%s email_hash=%s", institution_id, email_hash[:12])
    return PlainTextResponse(
        "You've been unsubscribed. You will no longer receive emails from this clinic."
    )


def _verify_resend_signature(raw_body: bytes, signature: str | None) -> None:
    """HMAC verify the Resend webhook (v1). Fails closed in production when the
    secret is unset; skipped only in local/test where the endpoint is firewalled."""
    secret = settings.resend_webhook_secret
    if not secret:
        if settings.is_production:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Webhook secret not configured")
        return
    if not signature:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing webhook signature")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    # Accept an optional "sha256=" prefix or a bare hex digest.
    provided = signature.split("=", 1)[-1].strip()
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid webhook signature")


def _recipients(data: dict[str, Any]) -> list[str]:
    to = data.get("to")
    if isinstance(to, list):
        return [str(x) for x in to if x]
    if isinstance(to, str):
        return [to]
    single = data.get("email") or data.get("email_id")
    return [str(single)] if single else []


@router.post("/webhooks/resend", status_code=status.HTTP_200_OK)
async def resend_webhook(request: Request) -> dict[str, Any]:
    """Resend bounce/complaint webhook → suppress email for the recipient.

    Always 200 for handled/ignored events (providers deactivate endpoints that
    return non-2xx); only a bad signature is a 403.
    """
    raw = await request.body()
    _verify_resend_signature(raw, request.headers.get("resend-signature") or request.headers.get("svix-signature"))

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON payload")

    event_type = str(payload.get("type", ""))
    if event_type not in _SUPPRESS_EVENTS:
        return {"status": "ignored", "type": event_type}

    data = payload.get("data") or {}
    institution_id = str((data.get("tags") or {}).get("institution_id") or data.get("institution_id") or "")
    suppressed = 0
    for email in _recipients(data):
        email_hash = hash_email(email)
        if not email_hash:
            continue
        if not institution_id:
            # No institution tag on the event — we cannot scope the suppression.
            logger.warning(
                "resend webhook %s missing institution scope; email_hash=%s skipped",
                event_type, hash_for_logging(email),
            )
            continue
        _enqueue_suppress(institution_id, email_hash, reason=f"resend_{event_type}")
        suppressed += 1

    logger.info("resend webhook: type=%s suppressed=%d", event_type, suppressed)
    return {"status": "processed", "type": event_type, "suppressed": suppressed}
