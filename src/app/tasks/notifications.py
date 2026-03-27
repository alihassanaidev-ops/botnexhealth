"""Background tasks for email notifications."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from src.app.config import settings
from src.app.database import get_db_session, init_database, is_database_initialized
from src.app.models.call import Call, CallStatus
from src.app.models.institution_location import InstitutionLocation
from src.app.models.user import InviteStatus, User, UserRole
from src.app.models.external_notification_recipient import ExternalNotificationRecipient
from src.app.models.user_email_notification_preference import UserEmailNotificationPreference
from src.app.services.email_notification_service import (
    EmailNotificationService,
    mask_phone,
    redact_patient_name,
    resolve_template_type,
)
from src.app.worker import celery_app

logger = logging.getLogger(__name__)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _unique_emails(emails: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in emails:
        email = (raw or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        out.append(email)
    return out


def _is_urgent(primary_tag: str | None, tags: list[str]) -> bool:
    urgent_tags = {CallStatus.EMERGENCY.value, CallStatus.COMPLAINT.value}
    if primary_tag in urgent_tags:
        return True
    return any(tag in urgent_tags for tag in tags)


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _pick_any(source: dict[str, Any], candidates: list[str]) -> str | None:
    if not source:
        return None
    canon = {_norm(str(k)): v for k, v in source.items()}
    for cand in candidates:
        value = canon.get(_norm(cand))
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"none", "n/a", "null"}:
            return text
    return None


def _extract_appointment_data(
    *,
    custom: dict[str, Any],
    dynamic: dict[str, Any],
    fallback_contact_name: str | None,
) -> dict[str, str | None]:
    patient_name = (
        _pick_any(dynamic, ["patient_name", "name"])  # explicit dynamic variables
        or _pick_any(custom, ["Patient name", "patient_name", "name"])
    )

    if not patient_name:
        first = _pick_any(dynamic, ["first_name"]) or _pick_any(custom, ["first_name"])
        last = _pick_any(dynamic, ["last_name"]) or _pick_any(custom, ["last_name"])
        if first:
            patient_name = f"{first} {last}".strip() if last else first

    if not patient_name:
        patient_name = fallback_contact_name

    appt_date = _pick_any(
        custom,
        ["Appointment Date", "appointment_date", "date", "appointmentDate"],
    ) or _pick_any(dynamic, ["appointment_date", "date"])
    appt_time = _pick_any(
        custom,
        ["Appointment Time", "appointment_time", "time", "appointmentTime"],
    ) or _pick_any(dynamic, ["appointment_time", "time"])
    appt_datetime = _pick_any(
        custom,
        ["Appointment DateTime", "Appointment Datetime", "appointment_datetime"],
    ) or _pick_any(dynamic, ["appointment_datetime"])

    if not appt_datetime and (appt_date or appt_time):
        appt_datetime = f"{appt_date or ''} {appt_time or ''}".strip()
    if not appt_datetime:
        appt_datetime = _pick_any(custom, ["Appointment Detail", "appointment_detail", "next_action"])

    provider = _pick_any(
        custom,
        ["Provider", "Provider Name", "provider", "provider_name", "doctor", "dentist"],
    ) or _pick_any(dynamic, ["provider", "provider_name"])
    service = _pick_any(
        custom,
        ["Service", "service", "appointment_type", "Appointment Type", "procedure"],
    ) or _pick_any(dynamic, ["service", "appointment_type"])

    return {
        "patient_name": patient_name,
        "appointment_datetime": appt_datetime,
        "appointment_provider": provider,
        "appointment_service": service,
    }


async def _resolve_recipients(
    session,
    institution_id: str,
    location_id: str | None,
    template_type: str | None = None,
) -> list[str]:
    filters = [
        User.institution_id == institution_id,
        User.is_active.is_(True),
        User.invite_status == InviteStatus.ACCEPTED.value,
    ]

    scoped_location_roles = [UserRole.LOCATION_ADMIN.value, UserRole.STAFF.value]
    role_scope = [User.role == UserRole.INSTITUTION_ADMIN.value]
    if location_id:
        role_scope.append(
            and_(
                User.location_id == location_id,
                User.role.in_(scoped_location_roles),
            )
        )

    # Build the platform user query
    user_query = select(User.email).where(*filters).where(or_(*role_scope))

    # Exclude users who opted out of this template type
    if template_type:
        opted_out = (
            select(UserEmailNotificationPreference.user_id).where(
                UserEmailNotificationPreference.template_type == template_type,
                UserEmailNotificationPreference.is_enabled.is_(False),
            )
        ).scalar_subquery()
        user_query = user_query.where(User.id.not_in(opted_out))

    result = await session.execute(user_query)
    db_emails = [row[0] for row in result.all() if row and row[0]]

    # Add external recipients for this template type
    if template_type:
        ext_result = await session.execute(
            select(ExternalNotificationRecipient.email).where(
                ExternalNotificationRecipient.institution_id == institution_id,
                ExternalNotificationRecipient.template_type == template_type,
                ExternalNotificationRecipient.is_active.is_(True),
            )
        )
        db_emails.extend(row[0] for row in ext_result.all() if row and row[0])

    fallback = _split_csv(settings.resend_alert_recipients)
    return _unique_emails(db_emails + fallback)


async def _send_call_notification_async(
    *,
    call_id: str,
    institution_id: str,
    location_id: str | None,
    analysis_snapshot: dict[str, Any] | None,
) -> None:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required to send call notifications")
    if not is_database_initialized():
        init_database(settings.database_url)

    async with get_db_session() as session:
        call = (
            await session.execute(
                select(Call)
                .where(Call.id == call_id, Call.institution_id == institution_id)
                .options(selectinload(Call.contact))
            )
        ).scalar_one_or_none()
        if not call:
            raise RuntimeError(f"Call not found for notification: {call_id}")

        location_name: str | None = None
        if location_id:
            location = (
                await session.execute(
                    select(InstitutionLocation).where(
                        InstitutionLocation.id == location_id,
                        InstitutionLocation.institution_id == institution_id,
                    )
                )
            ).scalar_one_or_none()
            if location:
                location_name = location.name

        # Determine template type BEFORE resolving recipients (needed for preference filtering)
        tags = _split_csv(call.call_tags)
        summary = (call.summary or "").strip() or None
        urgent = _is_urgent(call.call_status, tags)
        primary_tag = (call.call_status or "").lower().replace(" ", "_")
        is_appointment = primary_tag == "appointment_booked"
        template_type = resolve_template_type(is_urgent=urgent, is_appointment_booked=is_appointment)

        recipients = await _resolve_recipients(session, institution_id, location_id, template_type)
        if not recipients:
            logger.warning("No recipients configured for call notification (institution=%s)", institution_id)
            return

        analysis = analysis_snapshot or {}
        custom = analysis.get("custom_analysis_data") or {}
        dynamic = analysis.get("collected_dynamic_variables") or {}
        appt = _extract_appointment_data(
            custom=custom,
            dynamic=dynamic,
            fallback_contact_name=call.contact.full_name if call.contact else None,
        )

        payload = {
            "call_id": call.id,
            "institution_id": institution_id,
            "location_name": location_name,
            "caller_phone_masked": mask_phone(call.contact.phone if call.contact else None),
            "duration_seconds": call.call_duration_seconds,
            "primary_tag": call.call_status,
            "tags": tags,
            "summary": summary,
            "is_urgent": urgent,
            "appointment_patient_redacted": redact_patient_name(appt.get("patient_name")),
            "appointment_datetime": appt.get("appointment_datetime"),
            "appointment_provider": appt.get("appointment_provider"),
            "appointment_service": appt.get("appointment_service"),
        }

        idempotency_key = f"call-notification:{call.id}"
        sender = EmailNotificationService()
        await sender.send_notification(
            recipients=recipients,
            payload=payload,
            idempotency_key=idempotency_key,
            template_type=template_type,
            institution_id=institution_id,
        )

        logger.info(
            "Call notification sent: call=%s institution=%s recipients=%d urgent=%s",
            call.id,
            institution_id,
            len(recipients),
            urgent,
        )


@celery_app.task(
    name="src.app.tasks.notifications.send_call_notification",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_call_notification(
    self,  # noqa: ARG001 - required for bind=True retries
    call_id: str,
    institution_id: str,
    location_id: str | None = None,
    analysis_snapshot: dict[str, Any] | None = None,
) -> None:
    asyncio.run(
        _send_call_notification_async(
            call_id=call_id,
            institution_id=institution_id,
            location_id=location_id,
            analysis_snapshot=analysis_snapshot,
        )
    )


def enqueue_call_notification(
    *,
    call_id: str,
    institution_id: str,
    location_id: str | None,
    call_status: str | None,
    call_tags_csv: str | None,
    analysis_snapshot: dict[str, Any] | None,
) -> None:
    """Queue a background call-notification email task."""
    if not settings.celery_broker_url:
        logger.warning("CELERY_BROKER_URL is not set. Skipping call notification enqueue.")
        return

    tags = _split_csv(call_tags_csv)
    queue_name = "notifications_high" if _is_urgent(call_status, tags) else "notifications_default"

    send_call_notification.apply_async(
        kwargs={
            "call_id": call_id,
            "institution_id": institution_id,
            "location_id": location_id,
            "analysis_snapshot": analysis_snapshot or {},
        },
        queue=queue_name,
    )


@celery_app.task(
    name="src.app.tasks.notifications.send_test_call_notification",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def send_test_call_notification(
    self,  # noqa: ARG001 - required for bind=True retries
    recipients: list[str],
    institution_slug: str,
    requested_by: str | None = None,
    urgent: bool = False,
    tag: str | None = None,
    idempotency_key: str | None = None,
) -> None:
    async def _run() -> None:
        sender = EmailNotificationService()
        normalized_tag = ((tag or "").strip().lower().replace(" ", "_")) or (
            CallStatus.EMERGENCY.value if urgent else CallStatus.APPOINTMENT_BOOKED.value
        )
        payload = {
            "location_name": institution_slug,
            "caller_phone_masked": "******4321",
            "duration_seconds": 135,
            "primary_tag": normalized_tag,
            "tags": [normalized_tag],
            "summary": (
                "TEST NOTIFICATION: This is a synthetic call summary generated from the Super Admin panel."
            ),
            "is_urgent": urgent,
            "appointment_patient_redacted": "J*** D***",
            "appointment_datetime": "2026-03-10 2:30 PM",
            "appointment_provider": "Dr. Test Provider",
            "appointment_service": "Consultation",
        }
        await sender.send_call_created_notification(
            recipients=recipients,
            payload=payload,
            idempotency_key=idempotency_key or f"test-call:{institution_slug}:{uuid4()}",
        )
        logger.info(
            "Test call notification sent: institution=%s recipients=%d urgent=%s requested_by=%s",
            institution_slug,
            len(recipients),
            urgent,
            requested_by,
        )

    asyncio.run(_run())


def enqueue_test_call_notification(
    *,
    recipients: list[str],
    institution_slug: str,
    requested_by: str | None,
    urgent: bool,
    tag: str | None = None,
) -> None:
    """Queue a synthetic test notification email."""
    if not settings.celery_broker_url:
        raise RuntimeError("CELERY_BROKER_URL is not set")

    queue_name = "notifications_high" if urgent else "notifications_default"
    send_test_call_notification.apply_async(
        kwargs={
            "recipients": recipients,
            "institution_slug": institution_slug,
            "requested_by": requested_by,
            "urgent": urgent,
            "tag": tag,
            "idempotency_key": f"test-call-notification:{institution_slug}:{uuid4()}",
        },
        queue=queue_name,
    )
