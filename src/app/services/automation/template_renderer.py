"""Merge-variable renderer for automation workflow message templates.

Templates use {{var_name}} double-brace syntax. Unknown variables are replaced
with an empty string so patients never see raw placeholder text.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.models.contact import Contact
    from src.app.models.institution_location import InstitutionLocation

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def render_sms_body(
    template: str,
    contact: "Contact | None",
    location: "InstitutionLocation | None",
    context: dict,
) -> str:
    """Substitute {{var}} placeholders and return the rendered message body."""
    merge_vars: dict[str, str] = {}

    # Contact fields
    if contact is not None:
        merge_vars["patient_first_name"] = contact.first_name or ""
        merge_vars["patient_last_name"] = contact.last_name or ""
        merge_vars["patient_full_name"] = (
            contact.full_name
            or f"{contact.first_name or ''} {contact.last_name or ''}".strip()
        )

    # Location fields
    if location is not None:
        merge_vars["clinic_name"] = location.name or ""

    # Trigger metadata / campaign context (string-coerce values)
    for key, value in context.items():
        if isinstance(key, str) and key not in merge_vars:
            merge_vars[key] = str(value) if value is not None else ""

    def _replace(match: re.Match) -> str:
        return merge_vars.get(match.group(1), "")

    return _VAR_RE.sub(_replace, template)
