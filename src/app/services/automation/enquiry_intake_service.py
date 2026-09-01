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

from src.app.models.campaign_enquiry import CampaignEnquiry, EnquiryStatus
from src.app.models.contact import Contact
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
    enquiry: CampaignEnquiry
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
        # Resubmission. Fill blanks rather than overwrite: the later form may
        # know a phone the first did not, but a staff member's notes must not
        # be flattened by an automated repost.
        _fill_blanks(existing, first_name=first_name, last_name=last_name,
                     email=email, phone=phone, external_ref=external_ref)
        await _record_consent(
            session,
            institution_id=institution_id,
            location_id=location_id or existing.location_id,
            phone=phone or existing.phone,
            email=email or existing.email,
            channels=consent_channels,
            wording=consent_wording,
        )
        return IntakeResult(enquiry=existing, created=False)

    enquiry = CampaignEnquiry(
        id=str(uuid4()),
        institution_id=institution_id,
        location_id=location_id,
        intake_key=intake_key,
        source=source,
        first_name=(first_name or None),
        last_name=(last_name or None),
        status=EnquiryStatus.NEW.value,
        attribution=attribution or None,
        external_ref=external_ref,
    )
    # Through the setters, so the hashes are written with the values.
    enquiry.email = email
    enquiry.phone = phone
    if notes:
        enquiry.notes = notes

    contact = await _find_contact(
        session, institution_id=institution_id, phone=phone, email=email
    )
    if contact is not None:
        # Already known. Recorded now so the conversion step never creates a
        # second patient for somebody the practice already has.
        enquiry.contact_id = str(contact.id)

    session.add(enquiry)
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
    return IntakeResult(
        enquiry=enquiry, created=True, matched_existing_contact=contact is not None
    )


async def _find_existing(
    session: Any,
    *,
    institution_id: str,
    intake_key: str,
    phone: str | None,
    email: str | None,
) -> CampaignEnquiry | None:
    """The intake key first, then either identifier.

    The key is authoritative because the submitter controls it and it is what
    makes a repost idempotent. The hashes are the fallback for the same person
    arriving twice through different forms, which is the common case.
    """
    row = (
        await session.execute(
            select(CampaignEnquiry).where(
                CampaignEnquiry.institution_id == institution_id,
                CampaignEnquiry.intake_key == intake_key,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row

    phone_h = hash_phone(phone) if phone else None
    email_h = hash_email(email) if email else None
    for column, value in (
        (CampaignEnquiry.phone_hash, phone_h),
        (CampaignEnquiry.email_hash, email_h),
    ):
        if not value:
            continue
        row = (
            await session.execute(
                select(CampaignEnquiry).where(
                    CampaignEnquiry.institution_id == institution_id,
                    column == value,
                )
            )
        ).scalars().first()
        if row is not None:
            return row
    return None


async def _find_contact(
    session: Any, *, institution_id: str, phone: str | None, email: str | None
) -> Contact | None:
    """Is this lead already somebody the practice knows?"""
    phone_h = hash_phone(phone) if phone else None
    if not phone_h:
        return None
    return (
        await session.execute(
            select(Contact).where(
                Contact.institution_id == institution_id,
                Contact.phone_hash == phone_h,
                # An alias points at the record it was merged into; matching one
                # would attach the lead to a record staff already superseded.
                Contact.merged_into_id.is_(None),
            )
        )
    ).scalars().first()


def _fill_blanks(
    enquiry: CampaignEnquiry,
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
