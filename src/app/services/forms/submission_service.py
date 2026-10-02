"""Landing one submitted form response.

This is where a Meta lead or a Typeform response becomes a person in the system.
It reuses the existing intake path rather than writing a second one, so a lead
from a connected form is deduplicated against everybody the practice already
knows by exactly the same rules as a lead from the token endpoint: intake key
first, then either identifier hash, across every contact including patients.

The order matters and is deliberate:

1. **Claim the submission id first.** A provider that retries a delivery — and
   both of them do — must not create a second contact. The unique constraint on
   ``(institution, form, external_submission_id)`` is the claim; losing the race
   is a successful no-op, not an error.
2. **Refuse a lead with no way to reach them.** Nothing downstream could ever
   act, and a contact nobody can contact is a row that only causes confusion.
3. **Write the person, then the answers.** Custom field values hang off the
   contact, so it has to exist first.
4. **Consent comes from what the form declares**, not from the fact of somebody
   submitting it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.contact import Contact
from src.app.models.custom_field import CustomFieldValue, EntityType
from src.app.models.form_integration import (
    FormDefinition,
    FormSubmission,
    FormSubmissionStatus,
)
from src.app.services.automation.enquiry_intake_service import intake_enquiry
from src.app.services.forms.mapping_service import (
    MappedSubmission,
    apply_mapping,
    load_custom_field_definitions,
    load_mappings,
)
from src.app.services.forms.providers.base import NormalizedSubmission
from src.app.services.automation.canonical_context import merge_canonical_context
from src.app.services.retention_policy import (
    default_form_submission_raw_retain_until,
)

logger = logging.getLogger(__name__)


class SubmissionRejected(Exception):
    """The submission cannot become a lead, and retrying will not change that.

    Distinct from a transient failure: the caller answers a provider with 200 so
    it stops redelivering something that will never be accepted.
    """

    def __init__(self, message: str, *, external_submission_id: str | None = None):
        super().__init__(message)
        #: Carried so the caller can record *which* submission was refused.
        #: Without it the drop is a log line, and a clinic loses leads without
        #: ever being told.
        self.external_submission_id = external_submission_id


@dataclass
class LandedSubmission:
    submission: FormSubmission
    contact: Contact
    mapped: MappedSubmission
    #: False when this exact submission had already been landed. The caller uses
    #: it to avoid enrolling the same person twice from one redelivered webhook.
    created: bool
    #: True when the person was already in the practice software.
    matched_existing_contact: bool


async def land_submission(
    session: AsyncSession,
    *,
    form: FormDefinition,
    submission: NormalizedSubmission,
    raw_body: bytes | None = None,
) -> LandedSubmission | None:
    """Record a submission and the person behind it. ``None`` if already landed."""
    external_id = (submission.external_submission_id or "").strip()
    if not external_id:
        # Without one there is no idempotency, and a redelivery would create a
        # second contact. Falling back to a hash of the answers keeps a
        # well-behaved provider working and still collapses exact repeats.
        external_id = hashlib.sha256(
            json.dumps(submission.answers, sort_keys=True, default=str).encode()
        ).hexdigest()

    existing = (
        await session.execute(
            select(FormSubmission).where(
                FormSubmission.institution_id == form.institution_id,
                FormSubmission.form_id == form.id,
                FormSubmission.external_submission_id == external_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    mappings = await load_mappings(session, form_id=str(form.id))
    definitions = await load_custom_field_definitions(
        session,
        institution_id=str(form.institution_id),
        ids={
            str(mapping.target_custom_field_id)
            for mapping in mappings
            if mapping.target_custom_field_id
        },
    )
    mapped = apply_mapping(
        answers=submission.answers, mappings=mappings, definitions=definitions
    )
    if mapped.unmapped_keys:
        # Not an error — an unmapped question is meant to be dropped — but a
        # clinic wondering why an answer never appeared needs this to exist.
        logger.info(
            "form submission had %d unmapped answer(s) form=%s",
            len(mapped.unmapped_keys),
            form.id,
        )

    first_name, last_name = _resolve_name(mapped.contact_fields)
    email = mapped.contact_fields.get("email")
    phone = mapped.contact_fields.get("phone")
    if not email and not phone:
        raise SubmissionRejected(
            "The submission carried no email or phone. Map one of the form's "
            "questions to a contact method before enabling this form.",
            external_submission_id=external_id,
        )

    now = datetime.now(timezone.utc)
    # Scoped to the form so two forms that both collect the same person produce
    # one contact, matched on identifier, rather than colliding on intake key.
    intake_key = f"form:{form.id}:{external_id}"

    result = await intake_enquiry(
        session,
        institution_id=str(form.institution_id),
        location_id=str(form.location_id) if form.location_id else None,
        intake_key=intake_key,
        source=form.source_name or "external_form",
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        notes=mapped.contact_fields.get("notes"),
        attribution={
            "form_provider": form.provider,
            "form_id": str(form.id),
            "form_name": form.name,
            "external_form_id": form.external_form_id,
        },
        external_ref=external_id,
        consent_channels=_consent_channels(form),
        consent_wording=form.consent_wording,
    )
    contact = result.enquiry

    await _write_custom_field_values(
        session,
        institution_id=str(form.institution_id),
        contact_id=str(contact.id),
        mapped=mapped,
    )

    row = FormSubmission(
        id=str(uuid4()),
        institution_id=str(form.institution_id),
        location_id=str(form.location_id) if form.location_id else None,
        form_id=str(form.id),
        external_submission_id=external_id,
        contact_id=str(contact.id),
        context_answers=mapped.context_answers or None,
        status=FormSubmissionStatus.PROCESSED.value,
        submitted_at=submission.submitted_at,
        received_at=now,
    )
    if raw_body:
        row.raw_payload = raw_body.decode("utf-8", errors="replace")
        row.raw_retain_until = default_form_submission_raw_retain_until(now)
    session.add(row)

    try:
        await session.flush()
    except IntegrityError:
        # Two deliveries of the same response raced. The other one won and has
        # already done everything this one would have; that is the constraint
        # doing its job, not a failure to report.
        await session.rollback()
        logger.info(
            "form submission already landed by a concurrent delivery form=%s", form.id
        )
        return None

    form.last_submission_at = now
    await session.flush()

    return LandedSubmission(
        submission=row,
        contact=contact,
        mapped=mapped,
        created=result.created,
        matched_existing_contact=result.matched_existing_contact,
    )


async def record_unprocessed_submission(
    session: AsyncSession,
    *,
    form: FormDefinition,
    external_submission_id: str | None,
    status: str,
    reason: str,
    submitted_at: datetime | None = None,
    raw_body: bytes | None = None,
) -> FormSubmission | None:
    """Write down a submission we did not turn into a contact, and why.

    This is the difference between a clinic finding out and a clinic wondering.
    A lead that arrives for a switched-off form, or with nothing mapped to an
    email or a phone, used to leave a log line nobody reads; the practice sees
    leads stop and has no way to discover it. Now it lands as a row with a
    reason, and the Lead Forms screen counts them.

    The same unique constraint applies, so a redelivered drop is recorded once.
    Returns ``None`` when it was already recorded.
    """
    external_id = (external_submission_id or "").strip()
    if not external_id:
        # Nothing to key on, so recording it would grow a row per redelivery.
        logger.warning(
            "form submission dropped with no provider id form=%s: %s", form.id, reason
        )
        return None

    existing = (
        await session.execute(
            select(FormSubmission).where(
                FormSubmission.institution_id == form.institution_id,
                FormSubmission.form_id == form.id,
                FormSubmission.external_submission_id == external_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    now = datetime.now(timezone.utc)
    row = FormSubmission(
        id=str(uuid4()),
        institution_id=str(form.institution_id),
        location_id=str(form.location_id) if form.location_id else None,
        form_id=str(form.id),
        external_submission_id=external_id,
        contact_id=None,
        # No mapped answers: either the mapping is what failed, or the form was
        # never switched on and its map may be incomplete on purpose.
        context_answers=None,
        status=status,
        error_summary=reason[:500],
        submitted_at=submitted_at,
        received_at=now,
    )
    if raw_body:
        # Kept for the same window as a processed one. This is the case a
        # clinic most needs to inspect: the answers are the evidence of what
        # the mapping should have been.
        row.raw_payload = raw_body.decode("utf-8", errors="replace")
        row.raw_retain_until = default_form_submission_raw_retain_until(now)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return None
    return row


def _consent_channels(form: FormDefinition) -> tuple[str, ...]:
    """Only what the clinic declared this form's wording obtains."""
    channels: list[str] = []
    if form.consent_sms:
        channels.append("sms")
    if form.consent_email:
        channels.append("email")
    return tuple(channels)


def _resolve_name(contact_fields: dict[str, str]) -> tuple[str | None, str | None]:
    """Split a full name only when the form did not ask for the parts.

    A form with separate first/last questions is authoritative; splitting its
    full-name answer on top of that would overwrite "Mary Anne" with "Mary".
    """
    first = contact_fields.get("first_name")
    last = contact_fields.get("last_name")
    if first or last:
        return first, last
    parts = (contact_fields.get("full_name") or "").strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


async def _write_custom_field_values(
    session: AsyncSession,
    *,
    institution_id: str,
    contact_id: str,
    mapped: MappedSubmission,
) -> None:
    """Upsert each mapped custom field onto the contact.

    Overwrites rather than fills blanks: a person who resubmits a form is
    telling us their answer *now*, and a qualification workflow branching on a
    stale answer is worse than one branching on a changed one.
    """
    for definition, value in mapped.custom_field_values:
        row = (
            await session.execute(
                select(CustomFieldValue).where(
                    CustomFieldValue.field_definition_id == definition.id,
                    CustomFieldValue.entity_id == contact_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = CustomFieldValue(
                id=str(uuid4()),
                institution_id=institution_id,
                field_definition_id=str(definition.id),
                entity_type=EntityType.CONTACT.value,
                entity_id=contact_id,
            )
            session.add(row)
        row.set_value(value, is_phi=definition.is_phi)
    if mapped.custom_field_values:
        await session.flush()


def submission_trigger_context(
    *,
    form: FormDefinition,
    landed: LandedSubmission,
) -> dict[str, Any]:
    """The PHI-light context a form-submitted workflow run carries.

    ``form_answers`` is the flat shape a trigger filter or condition addresses
    (``form_answers.problem``), and it holds only answers mapped to non-PHI
    targets. Identity lives on the Contact, reachable through merge fields,
    which is the audited path — not through a copy of it sitting in run context.
    """
    contact_id = str(landed.contact.id)
    location_id = str(form.location_id) if form.location_id else None
    answers = dict(landed.mapped.context_answers or {})

    context: dict[str, Any] = {
        "event": "form.submitted",
        "trigger_type": "form_submitted",
        "contact_id": contact_id,
        "location_id": location_id,
        "form_provider": form.provider,
        "form_id": str(form.id),
        "form_external_id": form.external_form_id,
        "form_name": form.name,
        "form_submission_id": str(landed.submission.id),
        "form_external_submission_id": landed.submission.external_submission_id,
        "form_created_contact": bool(landed.created),
        "matched_existing_contact": bool(landed.matched_existing_contact),
        "enquiry_source": form.source_name or "external_form",
        "form_answers": answers,
        "form": {
            "id": str(form.id),
            "name": form.name,
            "provider": form.provider,
            "external_id": form.external_form_id,
            "answers": answers,
        },
    }
    patient_id = getattr(landed.contact, "nexhealth_patient_id", None)
    if patient_id:
        context["patient_id"] = patient_id
        context["nexhealth_patient_id"] = patient_id
    return merge_canonical_context(
        {key: value for key, value in context.items() if value is not None},
        # A connected form is the narrow case of a lead landing, so it shares
        # the enquiry vocabulary rather than inventing a parallel one.
        event_key="enquiry.received",
    )
