"""Signed email unsubscribe tokens (Plan 05).

A campaign email carries a one-click unsubscribe link whose token encodes the
``(institution_id, email_hash)`` it applies to, signed with the platform's keyed
hash (HMAC) — so the link can't be forged or point at another recipient, and the
raw email address never appears in the URL (only its keyed hash).
"""

from __future__ import annotations

import hmac

from src.app.services.sms_privacy import keyed_hash

_TOKEN_PURPOSE = "email-unsubscribe-token-v1"
_SIG_LEN = 32  # hex chars


def _signature(institution_id: str, email_hash: str) -> str:
    return keyed_hash(f"{institution_id}:{email_hash}", purpose=_TOKEN_PURPOSE, truncate_hex=_SIG_LEN)


def make_unsubscribe_token(institution_id: str, email_hash: str) -> str:
    """Return a signed token binding this institution + email identity."""
    return f"{institution_id}.{email_hash}.{_signature(institution_id, email_hash)}"


def verify_unsubscribe_token(token: str | None) -> tuple[str, str] | None:
    """Return ``(institution_id, email_hash)`` if the token is valid, else None."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    institution_id, email_hash, sig = parts
    if not institution_id or not email_hash or not sig:
        return None
    expected = _signature(institution_id, email_hash)
    if not hmac.compare_digest(expected, sig):
        return None
    return institution_id, email_hash


def unsubscribe_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/api/email/unsubscribe?token={token}"


def unsubscribe_footer(url: str, clinic_name: str | None = None) -> str:
    """Plain-text unsubscribe footer appended to every campaign email."""
    who = clinic_name or "this clinic"
    return (
        f"\n\n—\nYou're receiving this because you're a patient of {who}. "
        f"To stop receiving emails, unsubscribe here: {url}"
    )
