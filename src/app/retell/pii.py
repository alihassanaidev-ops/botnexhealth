"""Helpers for recognizing placeholders emitted by Retell PII scrubbing."""

from __future__ import annotations

import re
from typing import Any


_RETELL_PII_PLACEHOLDER_RE = re.compile(
    r"\[(?:(?:person name|address|email|ssn|passport|driver license|"
    r"credit card|bank account|password|pin|medical id|date of birth|"
    r"customer account number|phone number)(?:\s+\d+)?|pii info|redacted)\]",
    re.IGNORECASE,
)


def contains_retell_pii_placeholder(value: Any) -> bool:
    """Return whether a value contains a placeholder from Retell's scrubber."""
    return (
        isinstance(value, str) and _RETELL_PII_PLACEHOLDER_RE.search(value) is not None
    )
