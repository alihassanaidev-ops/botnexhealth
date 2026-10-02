"""Take in a lead and work out whether we already know them.

A lead is the one person in this system who is not a patient. Everything else
starts from a record in the practice software; this starts from a name and a way
to reach them, and possibly not even the name.

Two things make that awkward, and both are handled here rather than left to the
caller.

**Identity.** Deduplicating on email alone breaks the moment a channel other
than a web form is involved — a phone enquiry has no email, and a personal
address will not match the one on a work form. Deduplicating on phone alone
breaks on recycled numbers and missing country codes. So this matches on either
hash, and it also looks for an existing *patient contact*, because a "new lead"
is very often someone the clinic already has notes on. Converting without that
check is how a practice ends up with two charts for one person.

**Consent.** A lead has no relationship with the clinic, so nothing is implied.
Texting them is lawful only if the form captured an opt-in, and the wording it
showed is the evidence. ``consent_records`` already keys on ``phone_hash`` and
``email_hash`` with ``contact_id`` nullable, so a lead's consent is recorded in
the same place the send-time gates already read — no parallel mechanism, and no
risk of the two disagreeing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from src.app.models.contact import Contact, LeadStatus
from src.app.models.sms_consent import (
    ConsentBasis,
    ConsentChannel,
    ConsentRecord,
    ConsentSource,
    ConsentStatus,
)
from src.app.services.sms_privacy import hash_email, hash_phone, mask_phone

logger = logging.getLogger(__name__)


@dataclass
class IntakeResult:
    #: A lead is a contact. Named ``enquiry`` still because that is what the
    #: caller asked to record, not because it is a different kind of row.
    enquiry: Contact
    #: False when an earlier submission already created this one.
    created: bool
    #: True when the lead was matched to somebody already in the records.
    matched_existing_contact: bool = False


async def intake_enquiry(
    session: Any,
    *,
    institution_id: str,
    intake_key: str,
    source: str,
    location_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    attribution: dict | None = None,
    external_ref: str | None = None,
    notes: str | None = None,
    consent_channels: tuple[str, ...] = (),
    consent_wording: str | None = None,
) -> IntakeResult:
    """Record a lead, or return the one already recorded.

    ``consent_channels`` names what the submitter actually agreed to. Nothing is
    assumed from the mere act of submitting: a form that did not ask cannot be
    treated as having been answered.
    """
    existing = await _find_existing(
        session,
        institution_id=institution_id,
        intake_key=intake_key,
        phone=phone,
        email=email,
    )
    if existing is not None:
        # Already known — as a lead, a previous caller, or a patient. Fill
        # blanks rather than overwrite: a later form may know a phone the first
        # did not, but an automated repost must not flatten a staff note or
        # downgrade somebody who has since been registered.
        _fill_blanks(existing, first_name=first_name, last_name=last_name,
                     email=email, phone=phone, external_ref=external_ref)
        if existing.lead_status is None and existing.nexhealth_patient_id is None:
            existing.lead_status = LeadStatus.NEW.value
            existing.lead_source = existing.lead_source or source
        await _record_consent(
            session,
            institution_id=institution_id,
            location_id=location_id,
            phone=phone or existing.phone,
            email=email or existing.email,
            channels=consent_channels,
            wording=consent_wording,
        )
        return IntakeResult(
            enquiry=existing,
            created=False,
            # True when the person we matched is already in the practice
            # software, which is what tells the caller not to register them.
            matched_existing_contact=bool(existing.nexhealth_patient_id),
        )

    contact = Contact(
        institution_id=institution_id,
        first_name=(first_name or None),
        last_name=(last_name or None),
        full_name=" ".join(p for p in (first_name, last_name) if p).strip() or None,
        # No practice-software id. That single fact is what makes this a lead
        # rather than a patient, so nothing here invents one.
        nexhealth_patient_id=None,
        is_new_patient=True,
        lead_source=source,
        lead_status=LeadStatus.NEW.value,
        intake_key=intake_key,
        attribution=attribution or None,
        external_ref=external_ref,
    )
    # Through the setters, so the hashes are written with the values.
    contact.email = email
    contact.phone = phone
    if notes:
        contact.notes = notes

    session.add(contact)
    await session.flush()

    await _record_consent(
        session,
        institution_id=institution_id,
        location_id=location_id,
        phone=phone,
        email=email,
        channels=consent_channels,
        wording=consent_wording,
    )
    return IntakeResult(enquiry=contact, created=True, matched_existing_contact=False)


async def _find_existing(
    session: Any,
    *,
    institution_id: str,
    intake_key: str,
    phone: str | None,
    email: str | None,
) -> Contact | None:
    """The intake key first, then either identifier — across every contact.

    This is the part that got better by collapsing the two tables. An arriving
    lead used to be compared only against other enquiries, so somebody the
    practice had known for years came in as new. Now the same submission is
    matched against everybody: patients, previous callers and earlier leads
    alike, and the "already a patient" case falls out of the same lookup rather
    than needing a second one.
    """
    row = (
        await session.execute(
            select(Contact).where(
                Contact.institution_id == institution_id,
                Contact.intake_key == intake_key,
            )
        )
    ).scalars().first()
    if row is not None:
        return row

    phone_h = hash_phone(phone) if phone else None
    email_h = hash_email(email) if email else None
    for column, value in (
        (Contact.phone_hash, phone_h),
        (Contact.email_hash, email_h),
    ):
        if not value:
            continue
        row = (
            await session.execute(
                select(Contact).where(
                    Contact.institution_id == institution_id,
                    column == value,
                    # An alias points at the record it was merged into;
                    # matching one attaches the lead to a superseded row.
                    Contact.merged_into_id.is_(None),
                )
            )
        ).scalars().first()
        if row is not None:
            return row
    return None


def _fill_blanks(
    enquiry: Contact,
    *,
    first_name: str | None,
    last_name: str | None,
    email: str | None,
    phone: str | None,
    external_ref: str | None,
) -> None:
    if first_name and not enquiry.first_name:
        enquiry.first_name = first_name
    if last_name and not enquiry.last_name:
        enquiry.last_name = last_name
    if email and not enquiry.email_encrypted:
        enquiry.email = email
    if phone and not enquiry.phone_encrypted:
        enquiry.phone = phone
    if external_ref and not enquiry.external_ref:
        enquiry.external_ref = external_ref


async def _record_consent(
    session: Any,
    *,
    institution_id: str,
    location_id: str | None,
    phone: str | None,
    email: str | None,
    channels: tuple[str, ...],
    wording: str | None,
) -> None:
    """Write what the submitter agreed to, where the send gates already look.

    Basis is ``express``: they asked to be contacted. It is deliberately not
    ``implied`` — that is for an existing relationship, and a lead has none.
    """
    if not channels:
        return
    phone_h = hash_phone(phone) if phone else None
    email_h = hash_email(email) if email else None
    now = datetime.now(timezone.utc)
    for channel in channels:
        if channel in (ConsentChannel.SMS.value, ConsentChannel.VOICE.value) and not phone_h:
            continue
        if channel == ConsentChannel.EMAIL.value and not email_h:
            continue
        session.add(
            ConsentRecord(
                id=str(uuid4()),
                institution_id=institution_id,
                location_id=location_id,
                contact_id=None,  # not a patient yet; the hashes carry it
                channel=channel,
                phone_hash=phone_h,
                phone_masked=mask_phone(phone) if phone else None,
                email_hash=email_h,
                status=ConsentStatus.GRANTED.value,
                basis=ConsentBasis.EXPRESS.value,
                source=ConsentSource.SYSTEM.value,
                # The wording shown at submission is the evidence of what they
                # agreed to, so it is stored rather than summarised.
                reason=wording,
                created_at=now,
            )
        )
