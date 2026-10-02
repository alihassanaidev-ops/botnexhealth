"""What a form's questions mean here, and what a submission becomes.

Mapping is the piece the old intake route did not have, and its absence is what
made the whole thing guesswork: a payload arrived, a parser looked for an answer
whose declared type was ``email``, and everything it did not recognise was
dropped without anyone being told. A clinic could not see what a form asked, let
alone say that its "What's bothering you?" question is the thing a workflow
should branch on.

Two rules shape this file.

**Nothing is mapped by guessing alone.** Sync proposes a default mapping from
the question's declared type and its wording, because making a clinic hand-map
"Email" to email is busywork. But the proposal is written down as a row they can
see and change, and a question nobody mapped is ignored rather than
half-interpreted.

**Identifying answers do not reach the run context.** Everything mapped to a
Contact column, and every custom field the clinic marked as PHI, lands on the
record and stops there. What remains — the qualification answers, the
multiple-choice picks, the hidden UTM values — is what a workflow condition can
read. That split is why branching on "Problem" is safe and branching on the
patient's phone number is not something this offers.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.custom_field import CustomFieldDefinition, EntityType
from src.app.models.form_integration import (
    CONTACT_FIELD_KEYS,
    FormFieldMapping,
    FormFieldTarget,
)
from src.app.services.forms.providers.base import ProviderFormField

logger = logging.getLogger(__name__)

#: Provider field types that *are* the thing they claim to be. A question of one
#: of these types is proposed for the matching Contact column without needing
#: its wording to agree.
_TYPE_DEFAULTS: dict[str, str] = {
    "email": "email",
    "phone": "phone",
    "phone_number": "phone",
    "full_name": "full_name",
}

#: Wording fallbacks, applied only to free-text questions. Ordered: "first name"
#: has to be tested before the bare "name" that would also match it.
_LABEL_DEFAULTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bfirst\s*name\b|\bgiven\s*name\b", re.I), "first_name"),
    (re.compile(r"\blast\s*name\b|\bsurname\b|\bfamily\s*name\b", re.I), "last_name"),
    (re.compile(r"\bfull\s*name\b|^\s*name\s*$", re.I), "full_name"),
    (re.compile(r"\be-?mail\b", re.I), "email"),
    (re.compile(r"\bphone\b|\bmobile\b|\btelephone\b|\bcell\b", re.I), "phone"),
)

#: Free-text-ish provider types. Only these get the wording fallback: a
#: multiple-choice question titled "Phone or email?" is a qualification answer,
#: not a phone number.
_TEXT_TYPES = frozenset(
    {"short_text", "long_text", "text", "custom", "string", "hidden"}
)


@dataclass
class MappedSubmission:
    """One submission, resolved against the form's mapping."""

    #: Contact columns the submission fills in: first_name, email, phone, …
    contact_fields: dict[str, str] = field(default_factory=dict)
    #: ``(custom field definition, value)`` for each mapped custom field.
    custom_field_values: list[tuple[CustomFieldDefinition, str]] = field(
        default_factory=list
    )
    #: The answers a workflow may branch on. Never identifying.
    context_answers: dict[str, Any] = field(default_factory=dict)
    #: Questions with no mapping. Surfaced so a clinic can see what is being
    #: dropped rather than wondering why an answer never appeared.
    unmapped_keys: list[str] = field(default_factory=list)


def slugify(value: str) -> str:
    """A stable, readable context key from a question's wording."""
    slug = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return slug[:120] or "answer"


def default_contact_field(source: ProviderFormField) -> str | None:
    """The Contact column this question obviously is, or None.

    Type first, wording second, and wording only for free-text questions.
    """
    by_type = _TYPE_DEFAULTS.get((source.type or "").lower())
    if by_type:
        return by_type
    if (source.type or "").lower() not in _TEXT_TYPES:
        return None
    label = source.label or source.key
    for pattern, target in _LABEL_DEFAULTS:
        if pattern.search(label):
            return target
    return None


def build_default_mapping(
    *, institution_id: str, form_id: str, source: ProviderFormField
) -> FormFieldMapping:
    """The row sync writes for a newly discovered question.

    Anything not obviously an identifier starts as ``ignore``. That is the
    conservative direction: an ignored question is visibly unmapped in the UI,
    where a wrongly-guessed one silently writes the wrong thing.
    """
    contact_field = default_contact_field(source)
    if contact_field:
        return FormFieldMapping(
            institution_id=institution_id,
            form_id=form_id,
            source_key=source.key,
            source_label=source.label,
            source_type=source.type,
            target_kind=FormFieldTarget.CONTACT_FIELD.value,
            target_contact_field=contact_field,
            context_key=None,
        )
    return FormFieldMapping(
        institution_id=institution_id,
        form_id=form_id,
        source_key=source.key,
        source_label=source.label,
        source_type=source.type,
        target_kind=FormFieldTarget.IGNORE.value,
        context_key=None,
    )


def context_key_for(mapping: FormFieldMapping, definition: CustomFieldDefinition | None) -> str | None:
    """The key a mapped answer appears under in a workflow's run context.

    ``None`` means it does not appear there at all — which is the answer for
    every Contact column and every PHI custom field.
    """
    if mapping.target_kind != FormFieldTarget.CUSTOM_FIELD.value:
        return None
    if definition is None or definition.is_phi:
        return None
    return mapping.context_key or definition.field_key


async def load_mappings(
    session: AsyncSession, *, form_id: str
) -> list[FormFieldMapping]:
    return list(
        (
            await session.execute(
                select(FormFieldMapping)
                .where(FormFieldMapping.form_id == form_id)
                .order_by(FormFieldMapping.source_key)
            )
        )
        .scalars()
        .all()
    )


async def load_custom_field_definitions(
    session: AsyncSession, *, institution_id: str, ids: set[str]
) -> dict[str, CustomFieldDefinition]:
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                select(CustomFieldDefinition).where(
                    CustomFieldDefinition.institution_id == institution_id,
                    CustomFieldDefinition.id.in_(ids),
                    CustomFieldDefinition.entity_type == EntityType.CONTACT.value,
                )
            )
        )
        .scalars()
        .all()
    )
    return {str(row.id): row for row in rows}


def apply_mapping(
    *,
    answers: dict[str, Any],
    mappings: list[FormFieldMapping],
    definitions: dict[str, CustomFieldDefinition],
) -> MappedSubmission:
    """Turn raw provider answers into what gets written and what gets branched on."""
    result = MappedSubmission()
    by_key = {mapping.source_key: mapping for mapping in mappings}

    for key, raw_value in answers.items():
        mapping = by_key.get(key)
        if mapping is None:
            result.unmapped_keys.append(key)
            continue
        value = _stringify(raw_value)
        if value is None:
            continue

        if mapping.target_kind == FormFieldTarget.CONTACT_FIELD.value:
            target = (mapping.target_contact_field or "").strip()
            if target in CONTACT_FIELD_KEYS:
                result.contact_fields[target] = value
            continue

        if mapping.target_kind == FormFieldTarget.CUSTOM_FIELD.value:
            definition = definitions.get(str(mapping.target_custom_field_id or ""))
            if definition is None:
                # The clinic deleted the custom field but left the mapping.
                # Dropping the answer is right — there is nowhere to put it —
                # but it is worth a line, because to them the form still says
                # the question is mapped.
                logger.warning(
                    "form mapping points at a missing custom field: mapping=%s",
                    mapping.id,
                )
                continue
            result.custom_field_values.append((definition, value))
            context_key = context_key_for(mapping, definition)
            if context_key:
                result.context_answers[context_key] = _context_value(
                    raw_value, definition.field_type
                )
            continue

        # Explicitly ignored. Not an omission — the clinic said so.

    return result


def _stringify(value: Any) -> str | None:
    """One string per answer, because that is what both storage shapes hold.

    A multi-select arrives as a list; joining preserves every pick rather than
    keeping the first and quietly discarding the rest.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        parts = [_stringify(item) for item in value]
        joined = ", ".join(part for part in parts if part)
        return joined or None
    text = str(value).strip()
    return text or None


def _context_value(value: Any, field_type: str) -> Any:
    """The typed form of an answer, for comparison inside a workflow filter.

    A boolean question compared against ``true`` must not depend on whether the
    provider sent ``true``, ``"Yes"`` or ``"1"``, so it is normalised here
    rather than in every condition an author writes.
    """
    if field_type == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "yes", "y", "1", "on", "checked"}:
            return True
        if text in {"false", "no", "n", "0", "off", "unchecked"}:
            return False
        return None
    if field_type == "number":
        try:
            text = str(value).strip()
            return float(text) if "." in text else int(text)
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)):
        # Kept as a list so a condition can test membership rather than
        # substring-matching a joined string.
        return [str(item) for item in value if str(item).strip()]
    return _stringify(value)
