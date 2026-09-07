"""Merge-variable renderer for automation workflow message templates.

Templates use ``{{var_name}}`` double-brace syntax and accept two forms:

* a flat merge-field name — ``{{patient_first_name}}``
* a canonical context path — ``{{appointment.start_at}}``

The dotted form is the vocabulary the trigger picker and the condition editor
speak, so without it an author can see a field in one panel and be unable to use
it in the next. It also has to be understood to be *safe*: a pattern that does
not match a dotted token neither substitutes nor strips it, so
``{{appointment.status}}`` reached the patient as those literal characters.

A token may carry a fallback — ``{{appointment.type.name | "your appointment"}}``
— which is what lets a template use a field that is only sometimes present
without leaving a hole in the message.
"""

from __future__ import annotations

import re
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
#:
#: Kept in step with ``validation_service._TOKEN_RE`` so that what publish
#: validation inspects and what the renderer substitutes cannot disagree — they
#: previously did, in both directions.
_VAR_RE = re.compile(
    r"""\{\{\s*
        (?P<name>[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)
        (?:\s*\|\s*(?P<q>["'])(?P<default>.*?)(?P=q))?
    \s*\}\}""",
    re.VERBOSE,
)


def extract_tokens(template: str) -> list[str]:
    """Merge-variable names referenced by ``template``, in first-seen order.

    The token syntax lives here, so anything that needs to know which variables a
    template depends on — publish validation, the builder's preview — asks this
    rather than carrying its own copy of the pattern.
    """
    return list(
        dict.fromkeys(match.group("name") for match in _VAR_RE.finditer(template or ""))
    )


def _canonical_value(context: dict, path: str) -> Any:
    """Walk a dotted path through the raw run context."""
    cursor: Any = context
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


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


def build_render_vars(
    contact: "Contact | None",
    location: "InstitutionLocation | None",
    context: dict,
) -> dict[str, Any]:
    """Merge variables for the Jinja-rendered channels.

    Email renders through Jinja, which resolves ``{{appointment.status}}`` by
    attribute access — so the nested context objects have to be in scope or the
    template raises ``UndefinedError`` and the send fails outright. SMS resolves
    dotted paths itself and does not need this.

    Flat merge fields are layered on top, so a name collision keeps the
    catalog's formatted value rather than the raw context branch.
    """
    flat = build_merge_vars(contact, location, context)
    nested = {
        key: value
        for key, value in (context or {}).items()
        if isinstance(value, dict) and key not in flat
    }
    return {**nested, **flat}


def render_sms_body_reporting_blanks(
    template: str,
    contact: "Contact | None",
    location: "InstitutionLocation | None",
    context: dict,
) -> tuple[str, list[str]]:
    """Render the body, and name the tokens that resolved to nothing.

    Substitution stays permissive — a missing value becomes an empty string so a
    patient never receives raw ``{{token}}`` text. That is right for the patient
    and invisible to everyone else, which is why the blanks are reported back
    rather than merely tolerated: the caller records them on the step so a
    message that went out with a hole in it can be found afterwards.

    A token carrying a fallback is not a blank. The author has already said what
    to write when the value is missing, so nothing is missing.
    """
    merge_vars = build_merge_vars(contact, location, context)
    blanks: list[str] = []

    def _replace(match: re.Match) -> str:
        name = match.group("name")
        fallback = match.group("default")

        # A flat merge field wins over a context path of the same name: a
        # published template's ``{{appointment_date}}`` must keep meaning the
        # catalog's formatted date rather than a raw ISO timestamp.
        value = merge_vars.get(name, "")
        if not str(value).strip():
            raw = _canonical_value(context, name)
            value = "" if raw is None else str(raw)

        if not str(value).strip():
            if fallback is not None:
                return fallback
            if name not in blanks:
                blanks.append(name)
            return ""
        return str(value)

    return _VAR_RE.sub(_replace, template or ""), blanks


def render_sms_body(
    template: str,
    contact: "Contact | None",
    location: "InstitutionLocation | None",
    context: dict,
) -> str:
    """Substitute {{var}} placeholders and return the rendered message body.

    SMS deliberately stays on literal substitution rather than moving to the
    Jinja engine in ``services.template_engine``: SMS bodies gain nothing from
    conditionals, and re-rendering every published SMS template through a
    different parser is risk without benefit. Email uses the Jinja engine.
    """
    body, _ = render_sms_body_reporting_blanks(template, contact, location, context)
    return body


__all__ = [
    "MERGE_FIELD_CATALOG",
    "STATIC_MERGE_FIELDS",
    "MergeContextBuilder",
    "MergeFieldSpec",
    "build_merge_vars",
    "build_render_vars",
    "extract_tokens",
    "render_sms_body",
    "render_sms_body_reporting_blanks",
]
