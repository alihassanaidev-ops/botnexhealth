"""Presentation-time masking for Retell's PII-scrubbed call artifacts.

Retell's scrubbed transcript / summary replace detected PII with bracketed
placeholder tokens like ``[person 1]``, ``[email]``, ``[phone number]``. Those
read poorly in the dashboard, so before showing a scrubbed artifact we collapse
every ``[...]`` token to a fixed ``*****`` mask.

This is a *display* transform applied on top of an already-scrubbed value — it
is NOT a PII scrubber and must never be relied on to remove PII from raw text.
The raw (unscrubbed) variants stay encrypted at rest and behind the audited
reveal endpoints.
"""

from __future__ import annotations

import re
from typing import Any

# A Retell placeholder token: a bracketed run with no nested bracket, e.g.
# "[person 1]", "[email]", "[DATE_OF_BIRTH]". Non-greedy, single-line.
_BRACKET_TOKEN = re.compile(r"\[[^\[\]]+\]")

MASK = "*****"


def mask_brackets(text: str | None) -> str | None:
    """Replace every ``[...]`` placeholder token in ``text`` with ``*****``.

    Returns ``None`` unchanged so callers can pass a possibly-absent scrubbed
    value straight through.
    """
    if not text:
        return text
    return _BRACKET_TOKEN.sub(MASK, text)


def mask_transcript(turns: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Mask bracket tokens in the ``content`` of each transcript turn.

    Operates on a shallow copy of each turn so the stored value is never
    mutated. Non-content fields (role, tool-call name, etc.) pass through.
    """
    if not turns:
        return turns
    masked: list[dict[str, Any]] = []
    for turn in turns:
        if isinstance(turn, dict) and isinstance(turn.get("content"), str):
            new_turn = dict(turn)
            new_turn["content"] = mask_brackets(turn["content"])
            masked.append(new_turn)
        else:
            masked.append(turn)
    return masked
