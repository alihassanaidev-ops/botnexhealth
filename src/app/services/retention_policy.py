"""PHI retention policy enforcement.

This service handles irreversible PHI purges on the tables that store
clinical content outside the append-only audit log. It deliberately keeps
operational aggregates and non-patient metrics where possible, while clearing
patient-linked content after its retention clock expires.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import Settings, settings
from src.app.models.call import Call
from src.app.models.contact import Contact
from src.app.models.custom_field import CustomFieldValue, EntityType
from src.app.models.dead_letter_event import DeadLetterEvent
from src.app.models.notification import Notification
from src.app.models.sms_history_log import SmsHistoryLog
from src.app.services.sms_privacy import hash_for_logging, safe_error_summary

logger = logging.getLogger(__name__)


class RetentionClass(str, Enum):
    """Retention classes used in DB rows and S3 object tags."""

    CLINICAL_RECORD = "clinical_record"
    CLINICAL_RECORDING = "clinical_recording"
    SHORT_RECORDING = "short_recording"
    OPERATIONAL = "operational"


S3_RETENTION_TAG_KEY = "retention_class"
S3_SHORT_RECORDING_TAGGING = (
    f"{S3_RETENTION_TAG_KEY}={RetentionClass.SHORT_RECORDING.value}"
)


@dataclass(frozen=True)
class RetentionSummary:
    recordings_deleted: int = 0
    sms_bodies_purged: int = 0
    sms_rows_deleted: int = 0
    notifications_deleted: int = 0
    dead_letter_raw_payloads_purged: int = 0
    call_phi_purged: int = 0
    call_custom_fields_deleted: int = 0
    contacts_anonymized: int = 0
    contact_custom_fields_deleted: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "recordings_deleted": self.recordings_deleted,
            "sms_bodies_purged": self.sms_bodies_purged,
            "sms_rows_deleted": self.sms_rows_deleted,
            "notifications_deleted": self.notifications_deleted,
            "dead_letter_raw_payloads_purged": self.dead_letter_raw_payloads_purged,
            "call_phi_purged": self.call_phi_purged,
            "call_custom_fields_deleted": self.call_custom_fields_deleted,
            "contacts_anonymized": self.contacts_anonymized,
            "contact_custom_fields_deleted": self.contact_custom_fields_deleted,
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def retention_deadline(created_at: datetime, days: int) -> datetime:
    """Return an aware UTC deadline ``days`` after creation."""
    return _as_aware_utc(created_at) + timedelta(days=days)


def clinical_record_retain_until(
    created_at: datetime,
    *,
    date_of_birth: date | str | None = None,
    config: Settings = settings,
) -> datetime:
    """Compute the clinical record deadline.

    Baseline is 10 years from record creation. If a DOB is known, the
    deadline is extended until the patient turns the configured minor age
    threshold (28 by default), which covers the common PHIPA/CPSO-style
    "adult age plus 10 years" requirement.
    """
    deadline = retention_deadline(created_at, config.retention_clinical_record_days)
    dob = _coerce_date(date_of_birth)
    if dob is None:
        return deadline

    minor_deadline = datetime.combine(
        _add_years(dob, config.retention_minor_record_age_years),
        time.min,
        tzinfo=timezone.utc,
    )
    return max(deadline, minor_deadline)


def default_recording_retain_until(
    created_at: datetime,
    *,
    config: Settings = settings,
) -> datetime:
    return retention_deadline(created_at, config.retention_recording_days)


def default_sms_body_retain_until(
    created_at: datetime,
    *,
    config: Settings = settings,
) -> datetime:
    return retention_deadline(created_at, config.retention_sms_body_days)


def default_sms_row_retain_until(
    created_at: datetime,
    *,
    config: Settings = settings,
) -> datetime:
    # The row must survive at least as long as any retained body content.
    return max(
        retention_deadline(created_at, config.retention_sms_metadata_days),
        default_sms_body_retain_until(created_at, config=config),
    )


def default_notification_retain_until(
    created_at: datetime,
    *,
    config: Settings = settings,
) -> datetime:
    return retention_deadline(created_at, config.retention_notification_days)


def default_dead_letter_raw_retain_until(
    created_at: datetime,
    *,
    config: Settings = settings,
) -> datetime:
    return retention_deadline(created_at, config.retention_dead_letter_raw_days)


def s3_bucket_key_from_recording_url(
    recording_url: str | None,
    *,
    bucket: str | None,
    region: str,
) -> tuple[str, str] | None:
    """Extract the S3 bucket/key from the stored recording URL."""
    if not recording_url or not bucket:
        return None

    parsed = urlparse(recording_url)
    supported_hosts = {
        f"{bucket}.s3.{region}.amazonaws.com",
        f"{bucket}.s3.amazonaws.com",
    }
    if parsed.scheme != "https" or parsed.netloc not in supported_hosts:
        return None

    key = parsed.path.lstrip("/")
    if not key:
        return None
    return bucket, key


def build_expired_sms_body_update(now: datetime):
    return (
        update(SmsHistoryLog)
        .where(
            SmsHistoryLog.body_encrypted.is_not(None),
            SmsHistoryLog.body_purged_at.is_(None),
            SmsHistoryLog.body_retain_until <= now,
            _not_under_legal_hold(SmsHistoryLog.legal_hold_until, now),
        )
        .values(body_encrypted=None, body_purged_at=now)
    )


def build_expired_sms_row_delete(now: datetime):
    return delete(SmsHistoryLog).where(
        SmsHistoryLog.retain_until <= now,
        _not_under_legal_hold(SmsHistoryLog.legal_hold_until, now),
    )


def build_expired_notification_delete(now: datetime):
    return delete(Notification).where(
        Notification.retain_until <= now,
        _not_under_legal_hold(Notification.legal_hold_until, now),
    )


def build_expired_dead_letter_raw_update(now: datetime):
    return (
        update(DeadLetterEvent)
        .where(
            DeadLetterEvent.raw_payload_encrypted.is_not(None),
            DeadLetterEvent.raw_payload_purged_at.is_(None),
            DeadLetterEvent.raw_payload_retain_until <= now,
        )
        .values(raw_payload_encrypted=None, raw_payload_purged_at=now)
    )


def build_expired_recording_select(now: datetime):
    """Select calls whose recording must be deleted from S3.

    A recording is removed when either its own (short) retention clock or the
    parent call record's clock has expired. Including ``retain_until`` ensures
    a recording that was explicitly kept as part of the medical record is
    still cleaned up once the whole record expires, instead of orphaning the
    S3 object after the call PHI is purged.
    """
    return select(Call).where(
        Call.recording_url.is_not(None),
        Call.recording_deleted_at.is_(None),
        or_(
            Call.recording_retain_until <= now,
            Call.retain_until <= now,
        ),
        _not_under_legal_hold(Call.legal_hold_until, now),
    )


def build_expired_call_phi_update(now: datetime):
    # Recording fields are intentionally NOT cleared here — recordings are
    # owned exclusively by purge_expired_recordings, which deletes the S3
    # object before clearing the DB reference. contact_id is kept so the
    # call still links to its (separately anonymized) contact, preserving
    # distinct-patient analytics on historical dashboards.
    return (
        update(Call)
        .where(
            Call.purged_at.is_(None),
            Call.retain_until <= now,
            _not_under_legal_hold(Call.legal_hold_until, now),
        )
        .values(
            transcript_with_tool_calls_encrypted=None,
            summary_encrypted=None,
            retell_call_id=None,
            patient_intent=None,
            next_action=None,
            preferred_callback_datetime=None,
            callback_note=None,
            purged_at=now,
        )
    )


def build_purged_call_custom_field_delete():
    """Delete institution-defined custom field values for purged calls.

    Custom field values are an EAV side table keyed by ``entity_id`` (not a
    real FK), so they do not cascade. They can hold PHI the agent extracted
    from the webhook, so they must be deleted once their parent call is
    purged. Must run after build_expired_call_phi_update so the just-purged
    calls are included.
    """
    return delete(CustomFieldValue).where(
        CustomFieldValue.entity_type == EntityType.CALL.value,
        CustomFieldValue.entity_id.in_(
            select(Call.id).where(Call.purged_at.is_not(None))
        ),
    )


def build_contact_anonymize_update(now: datetime, *, config: Settings = settings):
    """Strip identifying fields from contacts whose calls are all purged.

    A contact is anonymized when it has no remaining retained call (every
    linked call is purged, or it has no calls) and is older than the
    clinical-record window. This is activity-based: a returning patient with
    any non-purged call — including one held back by a legal hold — keeps
    their identity record intact. The contact row and its NexHealth patient
    link are preserved so analytics survive and a future call re-populates it.
    """
    cutoff = now - timedelta(days=config.retention_clinical_record_days)
    has_retained_call = (
        select(Call.id)
        .where(Call.contact_id == Contact.id, Call.purged_at.is_(None))
        .exists()
    )
    return (
        update(Contact)
        .where(
            Contact.anonymized_at.is_(None),
            Contact.created_at <= cutoff,
            ~has_retained_call,
        )
        .values(
            first_name=None,
            last_name=None,
            full_name=None,
            email_encrypted=None,
            phone_encrypted=None,
            date_of_birth_encrypted=None,
            phone_hash=None,
            anonymized_at=now,
        )
    )


def build_anonymized_contact_custom_field_delete():
    """Delete contact-scoped custom field values for anonymized contacts.

    Must run after build_contact_anonymize_update so the just-anonymized
    contacts are included.
    """
    return delete(CustomFieldValue).where(
        CustomFieldValue.entity_type == EntityType.CONTACT.value,
        CustomFieldValue.entity_id.in_(
            select(Contact.id).where(Contact.anonymized_at.is_not(None))
        ),
    )


class RetentionPolicyService:
    """Apply configured retention windows for stored PHI."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        s3_client: Any | None = None,
        config: Settings = settings,
    ) -> None:
        self.session = session
        self.s3_client = s3_client
        self.config = config

    async def apply(self, now: datetime | None = None) -> dict[str, int]:
        """Apply every retention action and commit once at the end."""
        effective_now = _as_aware_utc(now or utc_now())

        summary = RetentionSummary(
            recordings_deleted=await self.purge_expired_recordings(effective_now),
            sms_bodies_purged=await self._execute_count(
                build_expired_sms_body_update(effective_now)
            ),
            sms_rows_deleted=await self._execute_count(
                build_expired_sms_row_delete(effective_now)
            ),
            notifications_deleted=await self._execute_count(
                build_expired_notification_delete(effective_now)
            ),
            dead_letter_raw_payloads_purged=await self._execute_count(
                build_expired_dead_letter_raw_update(effective_now)
            ),
            call_phi_purged=await self._execute_count(
                build_expired_call_phi_update(effective_now)
            ),
            # Runs after the call-PHI update so newly-purged calls are caught.
            call_custom_fields_deleted=await self._execute_count(
                build_purged_call_custom_field_delete()
            ),
            contacts_anonymized=await self._execute_count(
                build_contact_anonymize_update(effective_now, config=self.config)
            ),
            # Runs after contacts are anonymized so they are caught.
            contact_custom_fields_deleted=await self._execute_count(
                build_anonymized_contact_custom_field_delete()
            ),
        )
        await self.session.commit()
        return summary.as_dict()

    async def purge_expired_recordings(self, now: datetime) -> int:
        """Delete expired S3 recordings and clear their DB references."""
        result = await self.session.execute(build_expired_recording_select(now))
        calls = result.scalars().all()

        deleted = 0
        for call in calls:
            if self.s3_client is not None:
                location = s3_bucket_key_from_recording_url(
                    call.recording_url,
                    bucket=self.config.aws_s3_bucket_name,
                    region=self.config.aws_region,
                )
                if location is not None:
                    bucket, key = location
                    try:
                        self.s3_client.delete_object(Bucket=bucket, Key=key)
                    except Exception as exc:
                        logger.warning(
                            "Failed to delete expired recording from S3: call_hash=%s error=%s",
                            hash_for_logging(str(call.id)),
                            safe_error_summary(exc),
                        )
                        continue

            call.recording_url = None
            call.recording_deleted_at = now
            deleted += 1

        return deleted

    async def _execute_count(self, statement: Any) -> int:
        result = await self.session.execute(statement)
        return int(result.rowcount or 0)


def _not_under_legal_hold(column: Any, now: datetime):
    return or_(column.is_(None), column <= now)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _coerce_date(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # February 29 birthdays become February 28 in non-leap years.
        return value.replace(month=2, day=28, year=value.year + years)
