"""GoTracker Synchronizer webhook receiver for campaign data events."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.config import settings
from src.app.database import get_system_db_session
from src.app.models.contact import Contact
from src.app.models.gotracker_webhook_event import (
    GoTrackerWebhookEvent,
    GoTrackerWebhookStatus,
)
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.pms.gotracker.mappers import pid as gotracker_id
from src.app.services.dead_letter import capture_dead_letter
from src.app.services.retention_policy import default_gotracker_webhook_raw_retain_until
from src.app.services.sms_privacy import (
    payload_hash,
    redact_payload,
    safe_error_summary,
    sanitize_provider_error,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gotracker/webhooks", tags=["GoTracker Webhooks"])

_APPOINTMENT_EVENTS = frozenset(
    {"appointment.created", "appointment.updated", "appointment.cancelled"}
)
_PATIENT_EVENTS = frozenset({"patient.created", "patient.updated"})
_HANDLED_EVENTS = _APPOINTMENT_EVENTS | _PATIENT_EVENTS
_PROCESSING_TTL_SECONDS = 300
_SIGNATURE_TOLERANCE_SECONDS = 300


def _raw_payload_text(raw_body: bytes) -> str:
    return raw_body.decode("utf-8", errors="replace")


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """Verify `X-ScaleNexus-Signature: t=<unix>,v1=<hex>`."""
    secret = settings.gotracker_webhook_secret
    if not secret:
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="GoTracker webhook signature secret is not configured",
            )
        return
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing GoTracker webhook signature",
        )

    parts = {}
    for part in signature_header.split(","):
        key, sep, value = part.strip().partition("=")
        if sep and key and value:
            parts[key] = value
    timestamp_text = parts.get("t")
    signature = parts.get("v1")
    if not timestamp_text or not signature:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid GoTracker webhook signature format",
        )

    try:
        timestamp = int(timestamp_text)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid GoTracker webhook timestamp",
        )
    if abs(time.time() - timestamp) > _SIGNATURE_TOLERANCE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Stale GoTracker webhook signature",
        )

    signed = timestamp_text.encode("utf-8") + b"." + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid GoTracker webhook signature",
        )


@router.post("/{location_id}", status_code=status.HTTP_200_OK)
async def gotracker_webhook(location_id: str, request: Request) -> dict[str, Any]:
    """Handle GoTracker appointment and patient events for one local location."""
    raw_body = await request.body()
    _verify_signature(raw_body, request.headers.get("X-ScaleNexus-Signature"))

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload"
        )

    event = _event_name(payload)
    if event not in _HANDLED_EVENTS:
        logger.debug("gotracker_webhook: ignoring event=%s", event)
        return {"status": "ignored", "event": event}

    location = await _resolve_location(location_id)
    if location is None:
        logger.warning("gotracker_webhook: unknown location_id=%s event=%s", location_id, event)
        return {"status": "ignored", "reason": "unknown_location", "event": event}

    raw_payload = _raw_payload_text(raw_body)
    if event in _PATIENT_EVENTS:
        return await _process_patient_payload(
            event=event,
            payload=payload,
            raw_payload=raw_payload,
            location=location,
        )
    return await _process_appointment_payload(
        event=event,
        payload=payload,
        raw_payload=raw_payload,
        location=location,
    )


async def _resolve_location(location_id: str) -> InstitutionLocation | None:
    async with get_system_db_session(
        "gotracker_lookup", location_id=location_id
    ) as session:
        result = await session.execute(
            select(InstitutionLocation)
            .join(Institution, Institution.id == InstitutionLocation.institution_id)
            .where(
                InstitutionLocation.id == location_id,
                Institution.pms_type == "gotracker",
            )
        )
        return result.scalar_one_or_none()


async def _process_appointment_payload(
    *,
    event: str,
    payload: dict[str, Any],
    raw_payload: str,
    location: InstitutionLocation,
) -> dict[str, Any]:
    appointments = _appointment_payloads(payload)
    if not appointments:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Appointment payload missing required appointment object",
        )

    results = []
    for appointment in appointments:
        results.append(
            await _process_appointment_event(
                event=event,
                appointment=appointment,
                payload=payload,
                raw_payload=raw_payload,
                location=location,
            )
        )
    queued = sum(1 for result in results if result.get("status") == "queued")
    return {
        "status": "queued" if queued else "processed",
        "event": event,
        "processed": len(results),
        "queued": queued,
        "results": results,
    }


async def _process_appointment_event(
    *,
    event: str,
    appointment: dict[str, Any],
    payload: dict[str, Any],
    raw_payload: str,
    location: InstitutionLocation,
) -> dict[str, Any]:
    raw_appointment_id = _clean_str(
        _first(appointment, "id", "AppointmentId", "appointment_id")
    )
    raw_patient_id = _clean_str(
        _first(appointment, "patient_id", "PatientId", "ContactId", "contact_id")
    )
    start_time = _clean_str(
        _first(appointment, "start_time", "StartTime", "time", "appointment_time")
    )
    if not raw_appointment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Appointment payload missing required field: id",
        )

    is_cancelled = event == "appointment.cancelled" or bool(
        _first(appointment, "cancelled", "canceled", "Cancelled", "IsCancelled", default=False)
    )
    if not is_cancelled and not start_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Appointment payload missing required field: start_time",
        )

    appointment_id = gotracker_id(raw_appointment_id)
    patient_id = gotracker_id(raw_patient_id) if raw_patient_id else None
    provider_id = _prefixed_optional(
        _first(appointment, "provider_id", "ProviderId", "providerId")
    )
    appointment_type_id = _prefixed_optional(
        _first(
            appointment,
            "appointment_type_id",
            "AppointmentTypeId",
            "appointmentTypeId",
            "type_id",
            "TypeId",
        )
    )
    institution_id = str(location.institution_id)
    location_id = str(location.id)

    contact_id: str | None = None
    if patient_id:
        async with get_system_db_session(
            "gotracker_lookup", institution_id=institution_id, external_id=patient_id
        ) as session:
            result = await session.execute(
                select(Contact).where(
                    Contact.institution_id == institution_id,
                    Contact.nexhealth_patient_id == patient_id,
                )
            )
            contact = result.scalar_one_or_none()
            if contact:
                contact_id = str(contact.id)

    dedup_basis = (
        "cancelled"
        if is_cancelled
        else start_time
        or _clean_str(_first(appointment, "updated_at", "UpdatedAt"))
        or _dedup_fallback(payload)
    )
    dedup_key = f"{event}:{appointment_id}:{dedup_basis}"

    from src.app.services.automation.nexhealth_projection_service import (
        NexHealthProjectionService,
    )
    from src.app.services.automation.gotracker_subscription_service import (
        GoTrackerSubscriptionLifecycleService,
    )

    async with get_system_db_session(
        "gotracker_webhooks",
        institution_id=institution_id,
        location_id=location_id,
        external_id=appointment_id,
    ) as session:
        claimed = await _claim_event(
            session,
            institution_id=institution_id,
            location_id=location_id,
            appointment_id=appointment_id,
            patient_id=patient_id,
            event_type=event,
            dedup_key=dedup_key,
            source_event_id=_source_event_id(payload),
            payload=payload,
            raw_payload=raw_payload,
        )
        if not claimed:
            await session.commit()
            return {"status": "duplicate", "appointment_id": appointment_id}

        await GoTrackerSubscriptionLifecycleService(session).record_event_seen(
            institution_id=institution_id,
            location_id=location_id,
        )
        projection = NexHealthProjectionService(session)
        try:
            upsert = await projection.upsert_appointment(
                institution_id=institution_id,
                appointment_id=appointment_id,
                location_id=location_id,
                nexhealth_patient_id=patient_id,
                contact_id=contact_id,
                start_time=start_time,
                event=event,
                cancelled=is_cancelled,
                provider_id=provider_id,
                appointment_type_id=appointment_type_id,
            )
        except Exception as exc:  # noqa: BLE001
            return await _dead_letter_claimed_webhook(
                session=session,
                institution_id=institution_id,
                location_id=location_id,
                dedup_key=dedup_key,
                event=event,
                payload=payload,
                raw_payload=raw_payload,
                error=exc,
            )
        await _complete_event(session, institution_id=institution_id, dedup_key=dedup_key)
        await session.commit()

    if is_cancelled:
        from src.app.api.routes.nexhealth_webhooks import _cancel_runs_for_appointment

        runs_cancelled = await _cancel_runs_for_appointment(
            institution_id, appointment_id, reason="gotracker_appointment_cancelled"
        )
        return {
            "status": "cancelled",
            "appointment_id": appointment_id,
            "institution_id": institution_id,
            "runs_cancelled": runs_cancelled,
        }

    if upsert.change == "unchanged":
        return {"status": "unchanged", "appointment_id": appointment_id}

    runs_cancelled = 0
    if upsert.change == "rescheduled":
        from src.app.api.routes.nexhealth_webhooks import _cancel_runs_for_appointment

        runs_cancelled = await _cancel_runs_for_appointment(
            institution_id, appointment_id, reason="gotracker_appointment_rescheduled"
        )

    from src.app.tasks.automation_workflow import (
        resume_reactivation_booking,
        trigger_appointment_workflows,
    )

    trigger_appointment_workflows.delay(
        institution_id=institution_id,
        appointment_id=appointment_id,
        appointment_at_iso=start_time,
        contact_id=contact_id,
        location_id=location_id,
        trigger_metadata={
            "event": event,
            "source": "gotracker",
            "gotracker_appointment_id": raw_appointment_id,
        },
    )
    if contact_id:
        resume_reactivation_booking.delay(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=contact_id,
            appointment_id=appointment_id,
        )

    return {
        "status": "queued",
        "change": upsert.change,
        "appointment_id": appointment_id,
        "institution_id": institution_id,
        "runs_cancelled": runs_cancelled,
    }


async def _process_patient_payload(
    *,
    event: str,
    payload: dict[str, Any],
    raw_payload: str,
    location: InstitutionLocation,
) -> dict[str, Any]:
    patients = _patient_payloads(payload)
    if not patients:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient payload missing required patient object",
        )

    results = []
    for patient in patients:
        results.append(
            await _process_patient_event(
                event=event,
                patient=patient,
                payload=payload,
                raw_payload=raw_payload,
                location=location,
            )
        )
    return {
        "status": "processed",
        "event": event,
        "processed": len(results),
        "results": results,
    }


async def _process_patient_event(
    *,
    event: str,
    patient: dict[str, Any],
    payload: dict[str, Any],
    raw_payload: str,
    location: InstitutionLocation,
) -> dict[str, Any]:
    raw_patient_id = _clean_str(_first(patient, "id", "ContactId", "contact_id", "patient_id"))
    if not raw_patient_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient payload missing required field: id",
        )

    patient_id = gotracker_id(raw_patient_id)
    institution_id = str(location.institution_id)
    location_id = str(location.id)
    dedup_basis = (
        _clean_str(_first(patient, "updated_at", "UpdatedAt"))
        or _clean_str(payload.get("event_time"))
        or _dedup_fallback(payload)
    )
    dedup_key = f"{event}:{patient_id}:{dedup_basis}"

    from src.app.services.automation.nexhealth_projection_service import (
        NexHealthProjectionService,
    )
    from src.app.services.automation.gotracker_subscription_service import (
        GoTrackerSubscriptionLifecycleService,
    )

    async with get_system_db_session(
        "gotracker_webhooks",
        institution_id=institution_id,
        location_id=location_id,
        external_id=patient_id,
    ) as session:
        claimed = await _claim_event(
            session,
            institution_id=institution_id,
            location_id=location_id,
            patient_id=patient_id,
            event_type=event,
            dedup_key=dedup_key,
            source_event_id=_source_event_id(payload),
            payload=payload,
            raw_payload=raw_payload,
        )
        if not claimed:
            await session.commit()
            return {"status": "duplicate", "patient_id": patient_id}

        await GoTrackerSubscriptionLifecycleService(session).record_event_seen(
            institution_id=institution_id,
            location_id=location_id,
        )
        projection = NexHealthProjectionService(session)
        try:
            upsert = await projection.upsert_patient(
                institution_id=institution_id,
                patient=_patient_projection_payload(patient, patient_id=patient_id),
                local_location_ids=[location_id],
                nexhealth_location_ids=[
                    gotracker_id(_first(patient, "LocationId", "location_id", default=location_id))
                ],
                event=event,
            )
        except Exception as exc:  # noqa: BLE001
            return await _dead_letter_claimed_webhook(
                session=session,
                institution_id=institution_id,
                location_id=location_id,
                dedup_key=dedup_key,
                event=event,
                payload=payload,
                raw_payload=raw_payload,
                error=exc,
            )
        await _complete_event(session, institution_id=institution_id, dedup_key=dedup_key)
        await session.commit()

    return {
        "status": upsert.change,
        "patient_id": patient_id,
        "contact_id": str(upsert.contact.id),
        "institution_id": institution_id,
    }


async def _claim_event(
    session,
    *,
    institution_id: str,
    location_id: str | None,
    appointment_id: str | None = None,
    patient_id: str | None = None,
    event_type: str,
    dedup_key: str,
    source_event_id: str | None,
    payload: dict[str, Any],
    raw_payload: str,
) -> bool:
    existing = (
        await session.execute(
            select(GoTrackerWebhookEvent).where(
                GoTrackerWebhookEvent.institution_id == institution_id,
                GoTrackerWebhookEvent.dedup_key == dedup_key,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        now = datetime.now(timezone.utc)
        is_stale_processing = (
            existing.status == GoTrackerWebhookStatus.PROCESSING.value
            and existing.updated_at is not None
            and (now - _as_utc(existing.updated_at)).total_seconds() > _PROCESSING_TTL_SECONDS
        )
        if existing.status == GoTrackerWebhookStatus.FAILED.value or is_stale_processing:
            existing.status = GoTrackerWebhookStatus.PROCESSING.value
            existing.attempts += 1
            existing.updated_at = now
            _refresh_event_payload(
                existing,
                source_event_id=source_event_id,
                payload=payload,
                raw_payload=raw_payload,
                now=now,
            )
            return True
        return False

    now = datetime.now(timezone.utc)
    row = GoTrackerWebhookEvent(
        institution_id=institution_id,
        location_id=location_id,
        gotracker_appointment_id=appointment_id,
        gotracker_patient_id=patient_id,
        event_type=event_type,
        dedup_key=dedup_key,
        status=GoTrackerWebhookStatus.PROCESSING.value,
        attempts=1,
        source_event_id=source_event_id,
    )
    _refresh_event_payload(
        row,
        source_event_id=source_event_id,
        payload=payload,
        raw_payload=raw_payload,
        now=now,
    )
    session.add(row)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        return False
    return True


async def _complete_event(
    session,
    *,
    institution_id: str,
    dedup_key: str,
    error: str | None = None,
) -> GoTrackerWebhookEvent | None:
    row = (
        await session.execute(
            select(GoTrackerWebhookEvent).where(
                GoTrackerWebhookEvent.institution_id == institution_id,
                GoTrackerWebhookEvent.dedup_key == dedup_key,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.status = (
        GoTrackerWebhookStatus.FAILED.value if error
        else GoTrackerWebhookStatus.COMPLETED.value
    )
    row.last_error = sanitize_provider_error(error) if error else None
    row.updated_at = datetime.now(timezone.utc)
    return row


async def _dead_letter_claimed_webhook(
    *,
    session,
    institution_id: str,
    location_id: str | None,
    dedup_key: str,
    event: str,
    payload: dict[str, Any],
    raw_payload: str,
    error: Exception,
) -> dict[str, Any]:
    row = await _complete_event(
        session,
        institution_id=institution_id,
        dedup_key=dedup_key,
        error=str(error),
    )
    await session.commit()
    await capture_dead_letter(
        source="gotracker_webhook",
        event_type=event,
        error=error,
        payload=payload,
        raw_payload=raw_payload,
        attempts=row.attempts if row is not None else 1,
        institution_id=institution_id,
        location_id=location_id,
    )
    logger.warning(
        "gotracker_webhook: dead-lettered event=%s institution=%s location=%s error=%s",
        event,
        institution_id,
        location_id or "none",
        safe_error_summary(error),
    )
    return {
        "status": "failed",
        "event": event,
        "dead_lettered": True,
        "institution_id": institution_id,
        "location_id": location_id,
    }


def _refresh_event_payload(
    row: GoTrackerWebhookEvent,
    *,
    source_event_id: str | None,
    payload: dict[str, Any],
    raw_payload: str,
    now: datetime,
) -> None:
    if source_event_id:
        row.source_event_id = source_event_id
    row.payload_hash = payload_hash(payload)
    redacted = redact_payload(payload)
    row.redacted_payload = redacted if isinstance(redacted, dict) else {"payload": redacted}
    row.raw_payload = raw_payload
    row.raw_payload_retain_until = default_gotracker_webhook_raw_retain_until(now)


def _event_name(payload: dict[str, Any]) -> str:
    value = payload.get("event") or payload.get("event_name") or payload.get("type") or ""
    event = str(value).split(".complete", 1)[0].strip()
    return event.replace("_", ".") if "." not in event else event


def _source_event_id(payload: dict[str, Any]) -> str | None:
    for key in ("id", "event_id", "webhook_event_id", "delivery_id"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("id", "event_id", "webhook_event_id", "delivery_id"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _appointment_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    appointment = data.get("appointment")
    if isinstance(appointment, dict):
        return [appointment]
    appointments = data.get("appointments")
    if isinstance(appointments, list):
        return [item for item in appointments if isinstance(item, dict)]
    if _first(data, "id", "AppointmentId", "appointment_id") is not None:
        return [data]
    return []


def _patient_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    patient = data.get("patient") or data.get("contact")
    if isinstance(patient, dict):
        return [patient]
    patients = data.get("patients") or data.get("contacts")
    if isinstance(patients, list):
        return [item for item in patients if isinstance(item, dict)]
    if _first(data, "id", "ContactId", "contact_id", "patient_id") is not None:
        return [data]
    return []


def _patient_projection_payload(patient: dict[str, Any], *, patient_id: str) -> dict[str, Any]:
    first_name = _clean_str(_first(patient, "first_name", "FirstName", "firstName"))
    last_name = _clean_str(_first(patient, "last_name", "LastName", "lastName"))
    full_name = _clean_str(_first(patient, "name", "Name", "full_name", "FullName"))
    phone = _clean_str(_first(patient, "phone", "Phone", "PhoneNumber", "phone_number", "CellPhone"))
    return {
        "id": patient_id,
        "first_name": first_name,
        "last_name": last_name,
        "name": full_name or _join_name(first_name, last_name),
        "email": _clean_str(_first(patient, "email", "Email")),
        "preferred_language": _clean_str(
            _first(patient, "preferred_language", "PreferredLanguage")
        ),
        "inactive": bool(_first(patient, "inactive", "Inactive", "IsInactive", default=False)),
        "bio": {
            "phone_number": phone,
            "date_of_birth": _clean_str(_first(patient, "date_of_birth", "DateOfBirth", "DOB")),
            "new_patient": bool(_first(patient, "is_new_patient", "IsNewPatient", default=False)),
        },
    }


def _first(raw: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            return value
    return default


def _prefixed_optional(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return gotracker_id(value)


def _clean_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _join_name(first_name: str | None, last_name: str | None) -> str | None:
    name = " ".join(part for part in (first_name, last_name) if part)
    return name or None


def _dedup_fallback(payload: dict[str, Any]) -> str:
    return payload_hash(payload)[:32]


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
