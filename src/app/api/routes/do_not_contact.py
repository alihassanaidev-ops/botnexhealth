"""Institution-admin API for reviewing and releasing patient opt-outs.

The outbound gates persist channel opt-outs in three places for historical and
channel-specific reasons:

* SMS STOP replies create an active :class:`SmsSuppression`.
* Email and voice opt-outs are the latest revoked :class:`ConsentRecord` for
  that channel and identity.
* Staff-created, legacy DNC rows are channel-agnostic and remain visible as an
  ``all`` tag until released.

The list endpoint presents those sources as one patient-centric view. Releases
are addressed by opaque row ID and record type, so removing one channel never
requires exposing the patient's full phone or email and never clears another
channel accidentally.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_current_institution_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.contact import Contact
from src.app.models.sms_consent import (
    ConsentBasis,
    ConsentChannel,
    ConsentRecord,
    ConsentSource,
    ConsentStatus,
    DncScope,
    DoNotContact,
    SmsSuppression,
)
from src.app.models.user import User
from src.app.services.audit import log_audit
from src.app.services.sms_compliance import SmsComplianceService
from src.app.services.sms_privacy import hash_for_logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/institution/do-not-contact", tags=["Do Not Contact"])

DncChannel = Literal["sms", "voice", "email", "all"]
DncRecordType = Literal["sms_suppression", "consent_record", "do_not_contact"]


# ── API models ───────────────────────────────────────────────────────────────


class DncCreateRequest(BaseModel):
    """Backward-compatible staff entry point for an all-channel DNC."""

    phone: str = Field(min_length=3, description="Patient phone (E.164 preferred)")
    scope: Literal["location", "institution"] = "institution"
    location_id: str | None = None
    contact_id: str | None = None
    reason: str | None = Field(default=None, max_length=500)


class DncReleaseRequest(BaseModel):
    """Backward-compatible release by full phone number."""

    phone: str = Field(min_length=3)


class DncRecord(BaseModel):
    """Legacy all-channel DNC response returned by POST."""

    phone_masked: str
    scope: str
    source: str
    reason: str | None
    location_id: str | None
    contact_id: str | None
    created_at: datetime


class DncChannelRecord(BaseModel):
    id: str
    channel: DncChannel
    record_type: DncRecordType
    scope: str
    source: str
    reason: str | None
    location_id: str | None
    created_at: datetime


class DncPatientRecord(BaseModel):
    id: str
    contact_id: str | None
    patient_name: str | None
    phone_masked: str | None
    email_masked: str | None
    channels: list[DncChannelRecord]
    latest_opt_out_at: datetime


class DncListResponse(BaseModel):
    records: list[DncPatientRecord]


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("", response_model=DncRecord, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_WRITE)
async def add_do_not_contact(
    request: Request,
    body: DncCreateRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> DncRecord:
    """Record a staff-initiated, all-channel DNC for the selected scope."""
    institution_id = _institution_id(current_user)
    if body.scope == "location" and not body.location_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "location scope requires location_id"
        )

    async with get_db_session() as session:
        row = await SmsComplianceService(session).set_do_not_contact(
            institution_id=institution_id,
            phone=body.phone,
            scope=DncScope(body.scope),
            location_id=body.location_id,
            contact_id=body.contact_id,
            reason=body.reason,
            created_by_user_id=str(current_user.id),
        )
        await session.commit()
        record = _to_record(row)

    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.DO_NOT_CONTACT_CREATE,
        target_resource=f"do_not_contact:{hash_for_logging(body.phone)}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"scope": body.scope, "phone_hash": hash_for_logging(body.phone)},
        institution_id=institution_id,
        user_id=str(current_user.id),
        location_id=body.location_id,
    )
    logger.info(
        "do_not_contact created: institution=%s scope=%s phone_hash=%s by=%s",
        institution_id,
        body.scope,
        hash_for_logging(body.phone),
        current_user.id,
    )
    return record


@router.delete("", status_code=status.HTTP_200_OK)
@limiter.limit(RATE_WRITE)
async def remove_do_not_contact(
    request: Request,
    body: DncReleaseRequest,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> dict[str, bool]:
    """Backward-compatible release of an all-channel DNC by full phone."""
    institution_id = _institution_id(current_user)

    async with get_db_session() as session:
        released = await SmsComplianceService(session).release_do_not_contact(
            institution_id=institution_id,
            phone=body.phone,
            released_by_user_id=str(current_user.id),
        )
        await session.commit()

    if released is not None:
        await _audit_release(
            current_user=current_user,
            institution_id=institution_id,
            record_type="do_not_contact",
            record_id=str(released.id),
            channel="all",
            location_id=str(released.location_id) if released.location_id else None,
        )
    return {"released": released is not None}


@router.get("", response_model=DncListResponse)
@limiter.limit(RATE_READ)
async def list_do_not_contact(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> DncListResponse:
    """Return active opt-outs grouped into patient rows with channel tags."""
    institution_id = _institution_id(current_user)
    async with get_db_session() as session:
        return DncListResponse(
            records=await _list_patient_records(session, institution_id)
        )


@router.delete("/entries/{record_type}/{record_id}", status_code=status.HTTP_200_OK)
@limiter.limit(RATE_WRITE)
async def release_do_not_contact_entry(
    request: Request,
    record_type: DncRecordType,
    record_id: str,
    current_user: Annotated[User, Depends(get_current_institution_admin)],
) -> dict[str, bool]:
    """Release exactly one channel tag from the authenticated institution."""
    institution_id = _institution_id(current_user)
    user_id = str(current_user.id)

    async with get_db_session() as session:
        if record_type == "sms_suppression":
            released, channel, location_id = await _release_sms_suppression(
                session, institution_id, record_id, user_id
            )
        elif record_type == "consent_record":
            released, channel, location_id = await _release_revoked_consent(
                session, institution_id, record_id, user_id
            )
        else:
            released, channel, location_id = await _release_legacy_dnc(
                session, institution_id, record_id, user_id
            )
        await session.commit()

    if released:
        await _audit_release(
            current_user=current_user,
            institution_id=institution_id,
            record_type=record_type,
            record_id=record_id,
            channel=channel,
            location_id=location_id,
        )
    return {"released": released}


# ── Patient-centric list projection ──────────────────────────────────────────


async def _list_patient_records(
    session: AsyncSession, institution_id: str
) -> list[DncPatientRecord]:
    suppressions = (
        (
            await session.execute(
                select(SmsSuppression)
                .where(
                    SmsSuppression.institution_id == institution_id,
                    SmsSuppression.channel == ConsentChannel.SMS.value,
                    SmsSuppression.is_active.is_(True),
                )
                .order_by(SmsSuppression.created_at.desc())
                .limit(2000)
            )
        )
        .scalars()
        .all()
    )
    consent_rows = (
        (
            await session.execute(
                select(ConsentRecord)
                .where(
                    ConsentRecord.institution_id == institution_id,
                    ConsentRecord.channel.in_(
                        (
                            ConsentChannel.SMS.value,
                            ConsentChannel.VOICE.value,
                            ConsentChannel.EMAIL.value,
                        )
                    ),
                )
                .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    legacy_rows = (
        (
            await session.execute(
                select(DoNotContact)
                .where(
                    DoNotContact.institution_id == institution_id,
                    DoNotContact.is_active.is_(True),
                )
                .order_by(DoNotContact.created_at.desc())
                .limit(2000)
            )
        )
        .scalars()
        .all()
    )

    revoked_consents = _effective_revoked_consents(consent_rows)
    active_sms_keys = {
        (row.phone_hash, str(row.location_id) if row.location_id else None)
        for row in suppressions
    }
    revoked_consents = [
        row
        for row in revoked_consents
        if not (
            row.channel == ConsentChannel.SMS.value
            and (row.phone_hash, str(row.location_id) if row.location_id else None)
            in active_sms_keys
        )
    ]

    all_rows = [*suppressions, *revoked_consents, *legacy_rows]
    contacts = await _contacts_for_rows(session, institution_id, all_rows)
    contacts_by_id = {str(contact.id): contact for contact in contacts}
    contacts_by_phone: dict[str, list[Contact]] = defaultdict(list)
    for contact in contacts:
        if contact.phone_hash:
            contacts_by_phone[contact.phone_hash].append(contact)

    projected: dict[str, dict] = {}

    def add_entry(
        row: SmsSuppression | ConsentRecord | DoNotContact,
        *,
        channel: DncChannel,
        record_type: DncRecordType,
        scope: str,
        phone_hash: str | None,
        email_hash: str | None,
        phone_masked: str | None,
        email_masked: str | None,
    ) -> None:
        explicit_contact_id = str(row.contact_id) if row.contact_id else None
        contact = contacts_by_id.get(explicit_contact_id or "")
        if (
            contact is None
            and phone_hash
            and len(contacts_by_phone.get(phone_hash, [])) == 1
        ):
            contact = contacts_by_phone[phone_hash][0]
        contact_id = str(contact.id) if contact else explicit_contact_id
        identity_key = (
            f"contact:{contact_id}"
            if contact_id
            else f"phone:{phone_hash}"
            if phone_hash
            else f"email:{email_hash}"
            if email_hash
            else f"row:{row.id}"
        )
        patient_name = _contact_name(contact)
        entry = DncChannelRecord(
            id=str(row.id),
            channel=channel,
            record_type=record_type,
            scope=scope,
            source=row.source,
            reason=row.reason,
            location_id=str(row.location_id) if row.location_id else None,
            created_at=row.created_at,
        )
        if identity_key not in projected:
            projected[identity_key] = {
                "id": contact_id or str(row.id),
                "contact_id": contact_id,
                "patient_name": patient_name,
                "phone_masked": phone_masked,
                "email_masked": email_masked,
                "channels": [entry],
                "latest_opt_out_at": row.created_at,
            }
            return
        patient = projected[identity_key]
        patient["patient_name"] = patient["patient_name"] or patient_name
        patient["phone_masked"] = patient["phone_masked"] or phone_masked
        patient["email_masked"] = patient["email_masked"] or email_masked
        patient["channels"].append(entry)
        patient["latest_opt_out_at"] = max(patient["latest_opt_out_at"], row.created_at)

    for row in suppressions:
        add_entry(
            row,
            channel="sms",
            record_type="sms_suppression",
            scope="location" if row.location_id else "institution",
            phone_hash=row.phone_hash,
            email_hash=None,
            phone_masked=row.phone_masked,
            email_masked=None,
        )
    for row in revoked_consents:
        add_entry(
            row,
            channel=row.channel,  # type: ignore[arg-type]
            record_type="consent_record",
            scope="location" if row.location_id else "institution",
            phone_hash=row.phone_hash,
            email_hash=row.email_hash,
            phone_masked=row.phone_masked,
            email_masked=row.email_masked,
        )
    for row in legacy_rows:
        add_entry(
            row,
            channel="all",
            record_type="do_not_contact",
            scope=row.scope,
            phone_hash=row.phone_hash,
            email_hash=None,
            phone_masked=row.phone_masked,
            email_masked=None,
        )

    records = [DncPatientRecord(**patient) for patient in projected.values()]
    for patient in records:
        patient.channels.sort(
            key=lambda entry: (entry.channel, entry.location_id or "")
        )
    records.sort(key=lambda patient: patient.latest_opt_out_at, reverse=True)
    return records


def _effective_revoked_consents(rows: list[ConsentRecord]) -> list[ConsentRecord]:
    """Return consent rows whose revocation is currently effective.

    Institution-wide (``location_id IS NULL``) consent participates at every
    location. A newer global grant supersedes older location revocations; a
    newer location revocation can still override an older global grant for that
    location. This mirrors the gate's newest-applicable-record rule.
    """
    by_identity: dict[tuple[str, str], list[ConsentRecord]] = defaultdict(list)
    for row in rows:
        identity = (
            row.email_hash
            if row.channel == ConsentChannel.EMAIL.value
            else row.phone_hash
        )
        if identity:
            by_identity[(row.channel, identity)].append(row)

    effective: list[ConsentRecord] = []
    for identity_rows in by_identity.values():
        global_row = next(
            (row for row in identity_rows if row.location_id is None), None
        )
        location_rows: dict[str, ConsentRecord] = {}
        for row in identity_rows:
            if row.location_id is not None:
                location_rows.setdefault(str(row.location_id), row)

        if global_row and global_row.status == ConsentStatus.REVOKED.value:
            effective.append(global_row)
        for row in location_rows.values():
            global_is_newer = global_row is not None and (
                global_row.created_at,
                str(global_row.id),
            ) > (row.created_at, str(row.id))
            if row.status == ConsentStatus.REVOKED.value and not global_is_newer:
                effective.append(row)
    return effective


async def _contacts_for_rows(
    session: AsyncSession,
    institution_id: str,
    rows: list[SmsSuppression | ConsentRecord | DoNotContact],
) -> list[Contact]:
    contact_ids = {str(row.contact_id) for row in rows if row.contact_id}
    phone_hashes = {row.phone_hash for row in rows if getattr(row, "phone_hash", None)}
    predicates = []
    if contact_ids:
        predicates.append(Contact.id.in_(contact_ids))
    if phone_hashes:
        predicates.append(Contact.phone_hash.in_(phone_hashes))
    if not predicates:
        return []
    return (
        (
            await session.execute(
                select(Contact).where(
                    Contact.institution_id == institution_id,
                    or_(*predicates),
                )
            )
        )
        .scalars()
        .all()
    )


def _contact_name(contact: Contact | None) -> str | None:
    if contact is None:
        return None
    if contact.full_name:
        return contact.full_name
    name = " ".join(
        part for part in (contact.first_name, contact.last_name) if part
    ).strip()
    return name or None


# ── Per-entry release helpers ────────────────────────────────────────────────


async def _release_sms_suppression(
    session: AsyncSession, institution_id: str, record_id: str, user_id: str
) -> tuple[bool, str, str | None]:
    row = (
        await session.execute(
            select(SmsSuppression).where(
                SmsSuppression.id == record_id,
                SmsSuppression.institution_id == institution_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opt-out record not found")
    location_id = str(row.location_id) if row.location_id else None
    if not row.is_active:
        return False, ConsentChannel.SMS.value, location_id

    row.is_active = False
    row.released_by_user_id = user_id
    row.released_at = datetime.now(timezone.utc)
    await SmsComplianceService(session).record_consent_identity(
        institution_id=institution_id,
        location_id=location_id,
        contact_id=str(row.contact_id) if row.contact_id else None,
        phone_hash=row.phone_hash,
        phone_masked=row.phone_masked,
        status=ConsentStatus.GRANTED,
        channel=ConsentChannel.SMS,
        basis=ConsentBasis.IMPLIED,
        source=ConsentSource.MANUAL,
        reason="DNC page channel release",
        created_by_user_id=user_id,
    )
    return True, ConsentChannel.SMS.value, location_id


async def _release_revoked_consent(
    session: AsyncSession, institution_id: str, record_id: str, user_id: str
) -> tuple[bool, str, str | None]:
    row = (
        await session.execute(
            select(ConsentRecord).where(
                ConsentRecord.id == record_id,
                ConsentRecord.institution_id == institution_id,
            )
        )
    ).scalar_one_or_none()
    if row is None or row.channel not in {
        ConsentChannel.SMS.value,
        ConsentChannel.VOICE.value,
        ConsentChannel.EMAIL.value,
    }:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opt-out record not found")

    identity_filter = (
        ConsentRecord.email_hash == row.email_hash
        if row.channel == ConsentChannel.EMAIL.value
        else ConsentRecord.phone_hash == row.phone_hash
    )
    location_filter = (
        ConsentRecord.location_id.is_(None)
        if row.location_id is None
        else ConsentRecord.location_id == row.location_id
    )
    latest = (
        (
            await session.execute(
                select(ConsentRecord)
                .where(
                    ConsentRecord.institution_id == institution_id,
                    ConsentRecord.channel == row.channel,
                    identity_filter,
                    location_filter,
                )
                .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    location_id = str(row.location_id) if row.location_id else None
    if (
        latest is None
        or str(latest.id) != str(row.id)
        or latest.status != ConsentStatus.REVOKED.value
    ):
        return False, row.channel, location_id

    service = SmsComplianceService(session)
    if row.channel == ConsentChannel.EMAIL.value:
        await service.record_email_consent_identity(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=str(row.contact_id) if row.contact_id else None,
            email_hash=row.email_hash,
            email_masked=row.email_masked,
            status=ConsentStatus.GRANTED,
            basis=ConsentBasis.IMPLIED,
            source=ConsentSource.MANUAL,
            reason="DNC page channel release",
        )
    else:
        await service.record_consent_identity(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=str(row.contact_id) if row.contact_id else None,
            phone_hash=row.phone_hash,
            phone_masked=row.phone_masked,
            status=ConsentStatus.GRANTED,
            channel=row.channel,
            basis=ConsentBasis.IMPLIED,
            source=ConsentSource.MANUAL,
            reason="DNC page channel release",
            created_by_user_id=user_id,
        )
    return True, row.channel, location_id


async def _release_legacy_dnc(
    session: AsyncSession, institution_id: str, record_id: str, user_id: str
) -> tuple[bool, str, str | None]:
    row = (
        await session.execute(
            select(DoNotContact).where(
                DoNotContact.id == record_id,
                DoNotContact.institution_id == institution_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Opt-out record not found")
    location_id = str(row.location_id) if row.location_id else None
    if not row.is_active:
        return False, "all", location_id
    row.is_active = False
    row.released_by_user_id = user_id
    row.released_at = datetime.now(timezone.utc)
    return True, "all", location_id


async def _audit_release(
    *,
    current_user: User,
    institution_id: str,
    record_type: str,
    record_id: str,
    channel: str,
    location_id: str | None,
) -> None:
    await log_audit(
        actor=AuditActor.ADMIN,
        action=AuditAction.DO_NOT_CONTACT_RELEASE,
        target_resource=f"{record_type}:{record_id}",
        outcome=AuditOutcome.SUCCESS,
        metadata={"channel": channel, "record_type": record_type},
        institution_id=institution_id,
        user_id=str(current_user.id),
        location_id=location_id,
    )


def _institution_id(current_user: User) -> str:
    if not current_user.institution_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "User is not associated with an institution",
        )
    return str(current_user.institution_id)


def _to_record(row: DoNotContact) -> DncRecord:
    return DncRecord(
        phone_masked=row.phone_masked,
        scope=row.scope,
        source=row.source,
        reason=row.reason,
        location_id=str(row.location_id) if row.location_id else None,
        contact_id=str(row.contact_id) if row.contact_id else None,
        created_at=row.created_at,
    )
