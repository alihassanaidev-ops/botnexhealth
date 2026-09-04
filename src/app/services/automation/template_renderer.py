"""Merge-variable renderer for automation workflow message templates.

Templates use {{var_name}} double-brace syntax. Unknown variables are replaced
with an empty string so patients never see raw placeholder text.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.app.services.automation.merge_field_catalog import (
    MERGE_FIELD_CATALOG,
    STATIC_MERGE_FIELDS,
    MergeContextBuilder,
    MergeFieldSpec,
)

if TYPE_CHECKING:
    from src.app.models.contact import Contact
    from src.app.models.institution_location import InstitutionLocation

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def extract_tokens(template: str) -> list[str]:
    """Merge-variable names referenced by ``template``, in first-seen order.

    The token syntax lives here, so anything that needs to know which variables a
    template depends on — publish validation, the builder's preview — asks this
    rather than carrying its own copy of the pattern.
    """
    return list(dict.fromkeys(match.group(1) for match in _VAR_RE.finditer(template)))


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
    """
    merge_vars = build_merge_vars(contact, location, context)
    blanks: list[str] = []

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        value = merge_vars.get(name, "")
        if not str(value).strip() and name not in blanks:
            blanks.append(name)
        return value

    return _VAR_RE.sub(_replace, template), blanks


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
    "extract_tokens",
    "render_sms_body",
    "render_sms_body_reporting_blanks",
]
