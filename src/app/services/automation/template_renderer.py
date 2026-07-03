"""Merge-variable renderer for automation workflow message templates.

Templates use {{var_name}} double-brace syntax. Unknown variables are replaced
with an empty string so patients never see raw placeholder text.

The set of *static* merge fields (patient/clinic attributes) is defined once in
``STATIC_MERGE_FIELDS`` and drives BOTH this renderer and the merge-field catalog
API (``GET /automation/workflows/merge-fields``), so the two can never drift out
of sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.app.models.contact import Contact
    from src.app.models.institution_location import InstitutionLocation

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class MergeFieldSpec:
    """A static merge field the renderer knows how to substitute.

    ``source`` names the object that must be present for the field to resolve
    ("contact" or "location"); ``group`` is a display grouping for the builder
    UI; ``resolve`` produces the substituted string value.
    """

    name: str
    label: str
    description: str
    sample: str
    group: str
    source: str
    resolve: Callable[["Contact | None", "InstitutionLocation | None"], str]


def _full_name(contact: "Contact | None", _location: "InstitutionLocation | None") -> str:
    if contact is None:
        return ""
    return (
        contact.full_name
        or f"{contact.first_name or ''} {contact.last_name or ''}".strip()
    )


# Single source of truth for the static merge fields. Adding a field here
# automatically exposes it via the renderer AND the catalog endpoint.
STATIC_MERGE_FIELDS: tuple[MergeFieldSpec, ...] = (
    MergeFieldSpec(
        name="patient_first_name",
        label="Patient first name",
        description="The patient's first name.",
        sample="Jordan",
        group="patient",
        source="contact",
        resolve=lambda c, _l: (c.first_name or "") if c else "",
    ),
    MergeFieldSpec(
        name="patient_last_name",
        label="Patient last name",
        description="The patient's last name.",
        sample="Rivera",
        group="patient",
        source="contact",
        resolve=lambda c, _l: (c.last_name or "") if c else "",
    ),
    MergeFieldSpec(
        name="patient_full_name",
        label="Patient full name",
        description="The patient's full name.",
        sample="Jordan Rivera",
        group="patient",
        source="contact",
        resolve=_full_name,
    ),
    MergeFieldSpec(
        name="clinic_name",
        label="Clinic name",
        description="The name of the clinic/location.",
        sample="Riverside Dental",
        group="clinic",
        source="location",
        resolve=lambda _c, loc: (loc.name or "") if loc else "",
    ),
)


def render_sms_body(
    template: str,
    contact: "Contact | None",
    location: "InstitutionLocation | None",
    context: dict,
) -> str:
    """Substitute {{var}} placeholders and return the rendered message body."""
    merge_vars: dict[str, str] = {}

    # Static contact/clinic fields — only when their source object is present,
    # so a campaign context value can still fill an unresolved field.
    for field in STATIC_MERGE_FIELDS:
        present = contact is not None if field.source == "contact" else location is not None
        if present:
            merge_vars[field.name] = field.resolve(contact, location)

    # Trigger metadata / campaign context (string-coerce values); never override
    # a resolved static field.
    for key, value in context.items():
        if isinstance(key, str) and key not in merge_vars:
            merge_vars[key] = str(value) if value is not None else ""

    def _replace(match: re.Match) -> str:
        return merge_vars.get(match.group(1), "")

    return _VAR_RE.sub(_replace, template)
