"""Merge-variable renderer for automation workflow message templates.

Templates use ``{{var_name}}`` double-brace syntax and accept two forms:

* a flat merge-field name — ``{{patient_first_name}}``
* a canonical context path — ``{{appointment.start_at}}``

The dotted form is the one the trigger picker and condition editor speak, so
without it an author sees a field in one panel and cannot use it in the next.
Before it was supported the pattern did not match a dotted token at all, which
meant ``{{appointment.status}}`` was neither substituted nor stripped — it went
out in the message body verbatim.

A token may carry a fallback: ``{{appointment.type.name | "your appointment"}}``.
Resolution reports whether every token found a value, so a caller can refuse to
send a message with a hole in it rather than quietly delivering one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.app.services.automation.merge_field_catalog import (
    MERGE_FIELD_CATALOG,
    STATIC_MERGE_FIELDS,
    MergeContextBuilder,
    MergeFieldSpec,
)

if TYPE_CHECKING:
    from src.app.models.contact import Contact
    from src.app.models.institution_location import InstitutionLocation

#: ``{{ name }}``, ``{{ a.b.c }}``, optionally ``| "fallback"`` or ``| 'fallback'``.
_VAR_RE = re.compile(
    r"""\{\{\s*
        (?P<name>[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)
        (?:\s*\|\s*(?P<q>["'])(?P<default>.*?)(?P=q))?
    \s*\}\}""",
    re.VERBOSE,
)


@dataclass
class RenderResult:
    """A rendered body plus what could not be filled in."""

    text: str
    #: Tokens that resolved to nothing and had no fallback. A send with any of
    #: these would reach the patient with a gap where the detail should be.
    unresolved: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.unresolved


def extract_tokens(template: str) -> list[str]:
    """Token names used by a template, in order, deduplicated."""
    seen: list[str] = []
    for match in _VAR_RE.finditer(template or ""):
        name = match.group("name")
        if name not in seen:
            seen.append(name)
    return seen


def build_merge_vars(
    contact: "Contact | None",
    location: "InstitutionLocation | None",
    context: dict,
) -> dict[str, str]:
    """Resolve the merge variables available to a message template."""
    return MergeContextBuilder.build(
        contact=contact,
        location=location,
        context=MergeContextBuilder.normalize_raw_context(context),
    )


def _canonical_value(context: dict, path: str) -> Any:
    """Walk a dotted path through the raw run context."""
    cursor: Any = context
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def render_body(
    template: str,
    contact: "Contact | None",
    location: "InstitutionLocation | None",
    context: dict,
) -> RenderResult:
    """Substitute placeholders and report anything that could not be filled.

    Flat merge fields win over canonical paths for the same name, because a
    published template's ``{{appointment_date}}`` must keep meaning the
    catalog's formatted date rather than a raw ISO string.
    """
    merge_vars = build_merge_vars(contact, location, context)
    unresolved: list[str] = []

    def _replace(match: re.Match) -> str:
        name = match.group("name")
        fallback = match.group("default")

        value = merge_vars.get(name)
        if value in (None, ""):
            raw = _canonical_value(context, name)
            value = "" if raw is None else str(raw)

        if value == "":
            if fallback is not None:
                return fallback
            if name not in unresolved:
                unresolved.append(name)
            return ""
        return value

    return RenderResult(text=_VAR_RE.sub(_replace, template or ""), unresolved=unresolved)


def render_sms_body(
    template: str,
    contact: "Contact | None",
    location: "InstitutionLocation | None",
    context: dict,
) -> str:
    """Render a message body, ignoring completeness.

    SMS deliberately stays on literal substitution rather than moving to the
    Jinja engine in ``services.template_engine``: SMS bodies gain nothing from
    conditionals, and re-rendering every published SMS template through a
    different parser is risk without benefit. Email uses the Jinja engine.

    Callers that must not deliver a partially-filled message use
    :func:`render_body` and check ``complete``.
    """
    return render_body(template, contact, location, context).text


__all__ = [
    "MERGE_FIELD_CATALOG",
    "STATIC_MERGE_FIELDS",
    "MergeContextBuilder",
    "MergeFieldSpec",
    "RenderResult",
    "build_merge_vars",
    "extract_tokens",
    "render_body",
    "render_sms_body",
]
