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


def render_sms_body(
    template: str,
    contact: "Contact | None",
    location: "InstitutionLocation | None",
    context: dict,
) -> str:
    """Substitute {{var}} placeholders and return the rendered message body."""
    merge_vars = MergeContextBuilder.build(
        contact=contact,
        location=location,
        context=MergeContextBuilder.normalize_raw_context(context),
    )

    def _replace(match: re.Match) -> str:
        return merge_vars.get(match.group(1), "")

    return _VAR_RE.sub(_replace, template)


__all__ = [
    "MERGE_FIELD_CATALOG",
    "STATIC_MERGE_FIELDS",
    "MergeContextBuilder",
    "MergeFieldSpec",
    "render_sms_body",
]
