"""Privacy and compliance helpers for SMS workflows."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import phonenumbers

from src.app.security import keyed_hash


CASL_FOOTER = "Reply STOP to opt out. Reply HELP for help."
MAX_SMS_BODY_LENGTH = 1600

_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SAFE_IDENTIFIER_KEYS = {
    "id",
    "messagesid",
    "smsmessagesid",
    "message_sid",
    "sms_sid",
    "sid",
    "call_sid",
}


def _normalize_payload_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


_REDACTION_ALLOWLIST_KEYS = {
    _normalize_payload_key(key)
    for key in {
        *_SAFE_IDENTIFIER_KEYS,
        "account_sid",
        "event_id",
        "event_type",
        "request_id",
        "correlation_id",
        "webhook_id",
        "status",
        "provider_status",
        "error_code",
        "error_message_code",
        "attempt",
        "attempts",
        "retry_count",
        "direction",
        "channel",
        "created_at",
        "updated_at",
        "timestamp",
        "start_timestamp",
        "end_timestamp",
        "disconnection_reason",
        "duration_ms",
        "duration",
        "cost",
        "latency_ms",
        "num_segments",
        "num_media",
    }
}


def normalize_phone(phone: str | None, *, default_region: str = "CA") -> str:
    """Normalize a phone number for hashing and comparison."""
    if not phone:
        return ""
    try:
        parsed = phonenumbers.parse(phone, default_region)
        if not phonenumbers.is_valid_number(parsed):
            return ""
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        return ""


def hash_phone(phone: str | None) -> str | None:
    """Return the keyed phone hash used for PHI-safe lookup."""
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    return keyed_hash(normalized, purpose="phone-lookup-hash-v1")


def hash_for_logging(value: str | None) -> str:
    """Return a short keyed hash for logs and audit metadata."""
    if not value:
        return "none"
    return keyed_hash(value, purpose="sms-log-hash-v1", truncate_hex=16)


def payload_hash(payload: Any) -> str:
    """Return a keyed hash of a payload without storing the raw payload in logs."""
    try:
        text = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    except TypeError:
        text = str(payload)
    return keyed_hash(text, purpose="dead-letter-payload-hash-v1")


def mask_phone(phone: str | None) -> str:
    """Mask a phone number, preserving only the final four digits."""
    normalized = normalize_phone(phone)
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if not digits:
        return "Unknown"
    if len(digits) <= 4:
        return "****"
    prefix = "+" if normalized.startswith("+") else ""
    return f"{prefix}{'*' * (len(digits) - 4)}{digits[-4:]}"


def sanitize_provider_error(error: Exception | str | None, *, max_length: int = 300) -> str:
    """Remove likely PHI from provider error text before persistence/logging."""
    if not error:
        return "Unknown provider error"
    text = str(error)
    text = _PHONE_RE.sub("[phone-redacted]", text)
    text = _EMAIL_RE.sub("[email-redacted]", text)
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length] if len(text) > max_length else text


def redact_payload(payload: Any) -> Any:
    """Recursively redact PHI-ish values from payloads before storing in DLQ."""
    if isinstance(payload, Mapping):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            if _normalize_payload_key(key_text) in _REDACTION_ALLOWLIST_KEYS:
                redacted[key_text] = _redact_allowed_value(value)
            else:
                redacted[key_text] = "[redacted]"
        return redacted

    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return [redact_payload(item) for item in payload]

    return "[redacted]"


def prepare_outbound_sms_body(*, body: str, clinic_identity: str | None) -> str:
    """Apply clinic identity and CASL opt-out copy to an outbound SMS body."""
    message = (body or "").strip()
    if not message:
        raise ValueError("SMS body is required")

    identity = (clinic_identity or "Clinic").strip() or "Clinic"
    lower = message.lower()
    if not lower.startswith(f"{identity.lower()}:"):
        message = f"{identity}: {message}"

    if "reply stop" not in message.lower() or "reply help" not in message.lower():
        message = f"{message}\n{CASL_FOOTER}"

    if len(message) > MAX_SMS_BODY_LENGTH:
        raise ValueError(
            f"SMS body exceeds {MAX_SMS_BODY_LENGTH} characters after compliance copy"
        )
    return message


def _redact_allowed_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return redact_payload(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_payload(item) for item in value]
    return value
