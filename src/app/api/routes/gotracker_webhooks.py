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
_APPOINTMENT_WRITEBACK_EVENTS = frozenset(
    {"appointment.status_writeback.complete", "appointment.status_writeback.failed"}
)
_PATIENT_EVENTS = frozenset({"patient.created", "patient.updated"})
_HANDLED_EVENTS = _APPOINTMENT_EVENTS | _APPOINTMENT_WRITEBACK_EVENTS | _PATIENT_EVENTS
_PROCESSING_TTL_SECONDS = 300
_SIGNATURE_TOLERANCE_SECONDS = 300
_PMS_FOREIGN_ID_PREFIX = "tracker-"
_API_FOREIGN_ID_TYPE = "tracker-cloud-booked"
_WRITEBACK_FOREIGN_ID_TYPE = "tracker"


def _raw_payload_text(raw_body: bytes) -> str:
    return raw_body.decode("utf-8", errors="replace")


def _verify_signature(
    raw_body: bytes,
    signature_header: str | None,
    *,
    secret: str | None = None,
) -> None:
    """Verify `X-ScaleNexus-Signature: t=<unix>,v1=<hex>`."""
    secret = secret or settings.gotracker_webhook_secret
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

    location = await _resolve_location(location_id)
    if location is None:
        logger.warning("gotracker_webhook: unknown location_id=%s", location_id)
        return {"status": "ignored", "reason": "unknown_location"}

    _verify_signature(
        raw_body,
        request.headers.get("X-ScaleNexus-Signature"),
        secret=getattr(location, "gotracker_webhook_secret", None),
    )

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

    raw_payload = _raw_payload_text(raw_body)
    if event in _APPOINTMENT_WRITEBACK_EVENTS:
        return await _process_appointment_writeback_event(
            event=event,
            payload=payload,
            raw_payload=raw_payload,
            location=location,
        )
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
    start_time = _appointment_start_time(appointment)
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
    raw_provider_id = _clean_str(
        _first(appointment, "provider_id", "ProviderId", "providerId")
    )
    provider_id = gotracker_id(raw_provider_id) if raw_provider_id else None
    raw_appointment_type_id = _clean_str(
        _first(
            appointment,
            "appointment_type_id",
            "AppointmentTypeId",
            "appointmentTypeId",
            "type_id",
            "TypeId",
        )
    )
    appointment_type_id = (
        gotracker_id(raw_appointment_type_id) if raw_appointment_type_id else None
    )
    raw_schedule_column_id = _clean_str(
        _first(appointment, "schedule_column_id", "ScheduleColumnId", "scheduleColumnId")
    )
    raw_appointment_date = _clean_str(
        _first(appointment, "appointment_date", "AppointmentDate", "date", "Date")
    )
    raw_appointment_time = _clean_str(
        _first(appointment, "appointment_time", "AppointmentTime", "time", "Time")
    )
    embedded_patient = _embedded_patient_payload(appointment)
    raw_patient_first_name = _clean_str(
        _first(
            appointment,
            "patient_first_name",
            "PatientFirstName",
            "first_name",
            "FirstName",
            "firstName",
        )
        or (
            _first(
                embedded_patient,
                "patient_first_name",
                "PatientFirstName",
                "first_name",
                "FirstName",
                "firstName",
            )
            if embedded_patient is not None
            else None
        )
    )
    raw_patient_last_name = _clean_str(
        _first(
            appointment,
            "patient_last_name",
            "PatientLastName",
            "last_name",
            "LastName",
            "lastName",
        )
        or (
            _first(
                embedded_patient,
                "patient_last_name",
                "PatientLastName",
                "last_name",
                "LastName",
                "lastName",
            )
            if embedded_patient is not None
            else None
        )
    )
    raw_status_id = _clean_str(_first(appointment, "status_id", "StatusId", "statusId"))
    status_id = _clean_int(raw_status_id)
    duration = _clean_str(_first(appointment, "duration", "Duration"))
    booked_user_id = _clean_str(_first(appointment, "booked_user_id", "BookedUserId"))
    booked_timestamp = _clean_str(
        _first(appointment, "booked_timestamp", "BookedTimeStamp", "bookedTimeStamp")
    )
    created_machine_name = _clean_str(
        _first(appointment, "created_machine_name", "CreatedMachineName", "createdMachineName")
    )
    raw_appointment_context = {
        "is_preconfirmed": _first(appointment, "is_preconfirmed", "IsPreconfirmed"),
        "is_confirmed": _first(appointment, "is_confirmed", "IsConfirmed"),
        "master_id": _first(appointment, "master_id", "MasterId"),
        "original_date": _first(appointment, "original_date", "OriginalDate"),
        "detail": _first(appointment, "detail", "Detail"),
        "appointment_amount": _first(appointment, "appointment_amount", "AppointmentAmount"),
        "is_recall": _first(appointment, "is_recall", "IsRecall"),
        "is_personal": _first(appointment, "is_personal", "IsPersonal"),
        "is_all_day_appointment": _first(
            appointment, "is_all_day_appointment", "IsAllDayAppointment"
        ),
        "has_alarm": _first(appointment, "has_alarm", "HasAlarm"),
        "notify_time": _first(appointment, "notify_time", "NotifyTime"),
        "check_in": _first(appointment, "check_in", "CheckIn"),
        "in_chair": _first(appointment, "in_chair", "InChair"),
        "out_chair": _first(appointment, "out_chair", "OutChair"),
        "check_out": _first(appointment, "check_out", "CheckOut"),
        "flow_state": _first(appointment, "flow_state", "FlowState"),
        "flow_change": _first(appointment, "flow_change", "FlowChange"),
        "comments": _first(appointment, "comments", "Comments"),
        "booked_machine_name": _first(
            appointment, "booked_machine_name", "BookedMachineName"
        ),
        "created_user_id": _first(appointment, "created_user_id", "CreatedUserId"),
        "created_timestamp": _first(
            appointment, "created_timestamp", "CreatedTimeStamp"
        ),
        "modified_user_id": _first(appointment, "modified_user_id", "ModifiedUserId"),
        "modified_timestamp": _first(
            appointment, "modified_timestamp", "ModifiedTimeStamp"
        ),
        "modified_machine_name": _first(
            appointment, "modified_machine_name", "ModifiedMachineName"
        ),
        "rebook_info": _first(appointment, "rebook_info", "RebookInfo"),
        "confirmed_timestamp": _first(
            appointment, "confirmed_timestamp", "ConfirmedTimeStamp"
        ),
        "confirmed_user_id": _first(appointment, "confirmed_user_id", "ConfirmedUserId"),
        "confirmed_machine_name": _first(
            appointment, "confirmed_machine_name", "ConfirmedMachineName"
        ),
        "rebook_id": _first(appointment, "rebook_id", "RebookId"),
        "cancelled_timestamp": _first(
            appointment, "cancelled_timestamp", "CancelledTimeStamp"
        ),
        "cancelled_user_id": _first(
            appointment, "cancelled_user_id", "CancelledUserId"
        ),
        "cancelled_machine_name": _first(
            appointment, "cancelled_machine_name", "CancelledMachineName"
        ),
    }
    reasons = _appointment_reasons(appointment, payload)
    foreign_id_type = _foreign_id_type(payload, appointment)
    should_react = _is_pms_origin(foreign_id_type)
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
    source_event_id = _source_event_id(payload)
    dedup_key = _event_dedup_key(
        source_event_id=source_event_id,
        item_key=f"{event}:appointment:{appointment_id}",
        fallback=f"{event}:{appointment_id}:{dedup_basis}",
    )

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
            source_event_id=source_event_id,
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
        if contact_id is None and patient_id:
            patient_payload = _embedded_patient_payload(appointment)
            if patient_payload is not None:
                patient_projection = _patient_projection_payload(
                    patient_payload,
                    patient_id=patient_id,
                )
                patient_upsert = await projection.upsert_patient(
                    institution_id=institution_id,
                    patient=patient_projection,
                    local_location_ids=[location_id],
                    nexhealth_location_ids=[location_id],
                    event="appointment_embedded_patient",
                )
                contact_id = str(patient_upsert.contact.id)
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
                gotracker_status_id=status_id,
                is_confirmed=raw_appointment_context["is_confirmed"]
                if isinstance(raw_appointment_context["is_confirmed"], bool)
                else None,
                is_preconfirmed=raw_appointment_context["is_preconfirmed"]
                if isinstance(raw_appointment_context["is_preconfirmed"], bool)
                else None,
                status_source="webhook",
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

    if not should_react:
        if foreign_id_type == _API_FOREIGN_ID_TYPE:
            reason = "api_origin"
        elif foreign_id_type == _WRITEBACK_FOREIGN_ID_TYPE:
            reason = "writeback_confirmation"
        else:
            reason = "unrecognized_origin"

        log = logger.info if reason != "unrecognized_origin" else logger.warning
        log(
            "gotracker_webhook: projection-only event=%s appointment=%s "
            "foreign_id_type=%s",
            event,
            appointment_id,
            foreign_id_type or "missing",
        )
        return {
            "status": "projection_only",
            "reason": reason,
            "change": upsert.change,
            "appointment_id": appointment_id,
            "institution_id": institution_id,
        }

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
            institution_id,
            appointment_id,
            reason="gotracker_appointment_rescheduled",
            # A workflow-originated reschedule can race its returning webhook.
            # Keep the currently executing writeback run alive so it can finish;
            # pending/waiting runs for the old time are still cancelled.
            include_running=False,
        )

    from src.app.tasks.automation_workflow import (
        trigger_appointment_state_workflows,
        resume_reactivation_booking,
        trigger_appointment_workflows,
    )

    workflow_metadata = {
        "event": event,
        "source": "gotracker",
        "foreign_id_type": foreign_id_type,
        "gotracker_appointment_id": raw_appointment_id,
        "gotracker_contact_id": raw_patient_id,
        "contact_source_id": patient_id,
        "patient_first_name": raw_patient_first_name,
        "first_name": raw_patient_first_name,
        "patient_last_name": raw_patient_last_name,
        "last_name": raw_patient_last_name,
        "appointment_reason": reasons[0] if reasons else None,
        "appointment_reasons": reasons,
        "gotracker_reasons": reasons,
        "provider_id": provider_id,
        "gotracker_provider_id": raw_provider_id,
        "schedule_column_id": raw_schedule_column_id,
        "gotracker_schedule_column_id": raw_schedule_column_id,
        "appointment_status_id": raw_status_id,
        "gotracker_status_id": raw_status_id,
        "appointment_status": _gotracker_status_label(raw_status_id),
        "appointment_date": raw_appointment_date,
        "appointment_time": raw_appointment_time,
        "appointment_datetime": start_time,
        "appointment_duration": duration,
        "booked_user_id": booked_user_id,
        "booked_timestamp": booked_timestamp,
        "created_machine_name": created_machine_name,
        **raw_appointment_context,
        "gotracker_payload": {
            "event": event,
            "data": dict(appointment),
            "appointment": {
                "id": raw_appointment_id,
                "contact_id": raw_patient_id,
                "date": raw_appointment_date,
                "time": raw_appointment_time,
                "datetime": start_time,
                "reasons": reasons,
                "provider_id": raw_provider_id,
                "schedule_column_id": raw_schedule_column_id,
                "status_id": raw_status_id,
                "status": _gotracker_status_label(raw_status_id),
                "duration": duration,
                "booked_user_id": booked_user_id,
                "booked_timestamp": booked_timestamp,
                "created_machine_name": created_machine_name,
                **raw_appointment_context,
            },
        },
    }

    trigger_appointment_workflows.delay(
        institution_id=institution_id,
        appointment_id=appointment_id,
        appointment_at_iso=start_time,
        contact_id=contact_id,
        location_id=location_id,
        trigger_metadata=workflow_metadata,
    )
    if contact_id:
        resume_reactivation_booking.delay(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=contact_id,
            appointment_id=appointment_id,
        )
    confirmed_state = raw_appointment_context["is_confirmed"]
    preconfirmed_state = raw_appointment_context["is_preconfirmed"]
    if (
        status_id is not None
        or isinstance(confirmed_state, bool)
        or isinstance(preconfirmed_state, bool)
    ):
        trigger_appointment_state_workflows.delay(
            institution_id=institution_id,
            appointment_id=appointment_id,
            contact_id=contact_id,
            location_id=location_id,
            status_id=status_id,
            confirmed=confirmed_state if isinstance(confirmed_state, bool) else None,
            preconfirmed=preconfirmed_state if isinstance(preconfirmed_state, bool) else None,
            trigger_metadata=workflow_metadata,
        )

    return {
        "status": "queued",
        "change": upsert.change,
        "appointment_id": appointment_id,
        "institution_id": institution_id,
        "runs_cancelled": runs_cancelled,
    }


async def _process_appointment_writeback_event(
    *,
    event: str,
    payload: dict[str, Any],
    raw_payload: str,
    location: InstitutionLocation,
) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw_appointment_id = _clean_str(
        _first(data, "cloud_appointment_id", "appointment_id", "AppointmentId")
        or _first(payload, "foreign_id", "appointment_id", "AppointmentId")
    )
    if not raw_appointment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Writeback payload missing required appointment id",
        )

    appointment_id = gotracker_id(raw_appointment_id)
    institution_id = str(location.institution_id)
    location_id = str(location.id)
    source_event_id = _source_event_id(payload)
    fallback_basis = (
        _clean_str(_first(payload, "sequence_number", "occurred_at"))
        or _dedup_fallback(payload)
    )
    dedup_key = _event_dedup_key(
        source_event_id=source_event_id,
        item_key=f"{event}:writeback:{appointment_id}",
        fallback=f"{event}:{appointment_id}:{fallback_basis}",
    )

    from src.app.services.automation.gotracker_subscription_service import (
        GoTrackerSubscriptionLifecycleService,
    )
    from src.app.services.automation.gotracker_writeback_service import (
        GoTrackerAppointmentWritebackService,
    )
    from src.app.services.automation.nexhealth_projection_service import (
        NexHealthProjectionService,
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
            patient_id=None,
            event_type=event,
            dedup_key=dedup_key,
            source_event_id=source_event_id,
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

        writebacks = GoTrackerAppointmentWritebackService(session)
        projection = NexHealthProjectionService(session)
        if event.endswith(".failed"):
            pending = await writebacks.fail_latest(
                institution_id=institution_id,
                appointment_id=appointment_id,
                source_event_id=source_event_id,
                error=_clean_str(_first(data, "error")),
            )
            if pending is not None and pending.previous_start_time is not None:
                await projection.upsert_appointment(
                    institution_id=institution_id,
                    appointment_id=appointment_id,
                    location_id=location_id,
                    nexhealth_patient_id=None,
                    contact_id=pending.contact_id,
                    start_time=pending.previous_start_time.isoformat(),
                    event=event,
                    cancelled=False,
                    provider_id=pending.provider_id,
                    status_source="writeback_failed_restore",
                )
            await _complete_event(session, institution_id=institution_id, dedup_key=dedup_key)
            await session.commit()
            return {
                "status": "writeback_failed",
                "appointment_id": appointment_id,
                "institution_id": institution_id,
                "pending_writeback_found": pending is not None,
                "action": pending.action if pending is not None else None,
            }

        pending = await writebacks.complete_latest(
            institution_id=institution_id,
            appointment_id=appointment_id,
            source_event_id=source_event_id,
        )
        if pending is None:
            await _complete_event(session, institution_id=institution_id, dedup_key=dedup_key)
            await session.commit()
            return {
                "status": "ignored",
                "reason": "no_pending_writeback",
                "appointment_id": appointment_id,
                "institution_id": institution_id,
            }

        runs_cancelled = 0
        should_trigger_appointment = False
        should_trigger_state = False
        appointment_at_iso: str | None = None

        if pending.action == "reschedule":
            if pending.requested_start_time is not None:
                appointment_at_iso = pending.requested_start_time.isoformat()
                await projection.upsert_appointment(
                    institution_id=institution_id,
                    appointment_id=appointment_id,
                    location_id=location_id,
                    nexhealth_patient_id=None,
                    contact_id=pending.contact_id,
                    start_time=appointment_at_iso,
                    event=event,
                    cancelled=False,
                    provider_id=pending.provider_id,
                    gotracker_status_id=pending.status_id,
                    is_confirmed=pending.confirmed,
                    is_preconfirmed=pending.preconfirmed,
                    status_source="writeback_complete",
                )
                should_trigger_appointment = True
        elif pending.action == "cancel":
            await projection.upsert_appointment(
                institution_id=institution_id,
                appointment_id=appointment_id,
                location_id=location_id,
                nexhealth_patient_id=None,
                contact_id=pending.contact_id,
                start_time=(
                    pending.previous_start_time.isoformat()
                    if pending.previous_start_time is not None
                    else None
                ),
                event=event,
                cancelled=True,
                gotracker_status_id=pending.status_id,
                is_confirmed=pending.confirmed,
                is_preconfirmed=pending.preconfirmed,
                status_source="writeback_complete",
            )
        else:
            await projection.upsert_appointment(
                institution_id=institution_id,
                appointment_id=appointment_id,
                location_id=location_id,
                nexhealth_patient_id=None,
                contact_id=pending.contact_id,
                start_time=(
                    pending.previous_start_time.isoformat()
                    if pending.previous_start_time is not None
                    else None
                ),
                event=event,
                cancelled=False,
                gotracker_status_id=pending.status_id,
                is_confirmed=pending.confirmed,
                is_preconfirmed=pending.preconfirmed,
                status_source="writeback_complete",
            )
            should_trigger_state = (
                pending.status_id is not None
                or isinstance(pending.confirmed, bool)
                or isinstance(pending.preconfirmed, bool)
            )

        await _complete_event(session, institution_id=institution_id, dedup_key=dedup_key)
        await session.commit()

    if pending.action in {"reschedule", "cancel"}:
        from src.app.api.routes.nexhealth_webhooks import _cancel_runs_for_appointment

        runs_cancelled = await _cancel_runs_for_appointment(
            institution_id,
            appointment_id,
            reason=f"gotracker_writeback_{pending.action}",
            include_running=False,
        )

    if should_trigger_appointment and appointment_at_iso is not None:
        from src.app.tasks.automation_workflow import trigger_appointment_workflows

        trigger_appointment_workflows.delay(
            institution_id=institution_id,
            appointment_id=appointment_id,
            appointment_at_iso=appointment_at_iso,
            contact_id=pending.contact_id,
            location_id=location_id,
            trigger_metadata={
                "event": event,
                "source": "gotracker_writeback_complete",
                "gotracker_appointment_id": raw_appointment_id,
                "appointment_at": appointment_at_iso,
                "appointment_datetime": appointment_at_iso,
                "origin_workflow_run_id": pending.workflow_run_id,
            },
        )

    if should_trigger_state:
        from src.app.tasks.automation_workflow import trigger_appointment_state_workflows

        trigger_appointment_state_workflows.delay(
            institution_id=institution_id,
            appointment_id=appointment_id,
            contact_id=pending.contact_id,
            location_id=location_id,
            status_id=pending.status_id,
            confirmed=pending.confirmed,
            preconfirmed=pending.preconfirmed,
            trigger_metadata={
                "event": event,
                "source": "gotracker_writeback_complete",
                "gotracker_appointment_id": raw_appointment_id,
            },
        )

    return {
        "status": "writeback_completed",
        "appointment_id": appointment_id,
        "institution_id": institution_id,
        "action": pending.action,
        "runs_cancelled": runs_cancelled,
        "appointment_triggered": should_trigger_appointment,
        "state_triggered": should_trigger_state,
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
    source_event_id = _source_event_id(payload)
    dedup_key = _event_dedup_key(
        source_event_id=source_event_id,
        item_key=f"{event}:patient:{patient_id}",
        fallback=f"{event}:{patient_id}:{dedup_basis}",
    )

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
            source_event_id=source_event_id,
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
    raw = str(value).strip()
    if raw.startswith("appointment.status_writeback."):
        return raw
    event = raw.split(".complete", 1)[0]
    return event.replace("_", ".") if "." not in event else event


def _source_event_id(payload: dict[str, Any]) -> str | None:
    for key in ("id", "event_id", "webhook_event_id", "delivery_id"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        # A nested `data.id` is commonly the appointment/patient ID, not the
        # webhook delivery ID. Only explicit event-ID fields are safe here.
        for key in ("event_id", "webhook_event_id", "delivery_id"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _event_dedup_key(
    *,
    source_event_id: str | None,
    item_key: str,
    fallback: str,
) -> str:
    """Prefer GoTracker's delivery ID while remaining safe for batch payloads."""
    if not source_event_id:
        return fallback
    item_digest = hashlib.sha256(item_key.encode("utf-8")).hexdigest()[:16]
    return f"source:{source_event_id}:{item_digest}"


def _foreign_id_type(
    payload: dict[str, Any], entity: dict[str, Any] | None = None
) -> str | None:
    """Read the GoTracker mutation origin from supported webhook shapes."""
    candidates: list[dict[str, Any]] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
    if entity is not None:
        candidates.append(entity)

    for candidate in candidates:
        value = _first(
            candidate,
            "foreign_id_type",
            "foreignIdType",
            "ForeignIdType",
        )
        cleaned = _clean_str(value)
        if cleaned:
            return cleaned.casefold()
    return None


def _is_pms_origin(foreign_id_type: str | None) -> bool:
    """Whether an appointment event came from a human using Tracker PMS.

    Legacy installed agents omit ``foreign_id_type`` entirely. Newer agents use
    ``tracker-<PracticeId>``; the PracticeId is installation-local and is only an
    origin marker, never a tenant identifier.
    """
    if foreign_id_type is None:
        return True
    if not foreign_id_type.startswith(_PMS_FOREIGN_ID_PREFIX):
        return False
    practice_id = foreign_id_type.removeprefix(_PMS_FOREIGN_ID_PREFIX)
    return bool(practice_id) and practice_id.isdigit()


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


def _appointment_start_time(appointment: dict[str, Any]) -> str | None:
    direct = _clean_str(
        _first(
            appointment,
            "start_time",
            "StartTime",
            "time",
            "appointment_time",
            "AppointmentTimeStamp",
            "AppointmentDateTime",
        )
    )
    if direct:
        return direct

    appointment_date = _clean_str(_first(appointment, "AppointmentDate", "date", "Date"))
    appointment_time = _clean_str(_first(appointment, "AppointmentTime", "time", "Time"))
    if not appointment_date or not appointment_time:
        return None

    date_part = appointment_date.split("T", 1)[0]
    time_part = appointment_time.split("T", 1)[-1].removesuffix("Z")
    return f"{date_part}T{time_part}Z"


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


def _appointment_reasons(
    appointment: dict[str, Any], payload: dict[str, Any]
) -> list[str]:
    raw = _first(
        appointment,
        "reasons",
        "Reasons",
        "reason",
        "Reason",
        "appointment_reasons",
        "AppointmentReasons",
        "appointment_reason",
        "AppointmentReason",
    )
    if raw is None:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        raw = _first(
            data,
            "reasons",
            "Reasons",
            "reason",
            "Reason",
            "appointment_reasons",
            "AppointmentReasons",
            "appointment_reason",
            "AppointmentReason",
        )
    return _string_list(raw)


def _gotracker_status_label(status_id: str | None) -> str | None:
    if not status_id:
        return None
    return {
        "1": "booked",
        "2": "booked_waiting",
        "3": "cancelled",
        "4": "late",
        "5": "no_show",
        "6": "office_cancel",
        "7": "pending",
        "8": "short_cancel",
        "9": "waiting",
    }.get(str(status_id), str(status_id))


def _string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [text for item in value if (text := _clean_str(item))]
    if isinstance(value, dict):
        return [text for item in value.values() if (text := _clean_str(item))]
    text = _clean_str(value)
    return [text] if text else []


def _embedded_patient_payload(appointment: dict[str, Any]) -> dict[str, Any] | None:
    patient = appointment.get("patient") or appointment.get("contact") or appointment.get("Patient")
    if isinstance(patient, dict):
        return patient
    patient_keys = {
        "first_name",
        "FirstName",
        "last_name",
        "LastName",
        "name",
        "Name",
        "phone",
        "Phone",
        "PhoneNumber",
        "CellPhone",
        "email",
        "Email",
    }
    return appointment if any(key in appointment for key in patient_keys) else None


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


def _clean_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _clean_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _join_name(first_name: str | None, last_name: str | None) -> str | None:
    name = " ".join(part for part in (first_name, last_name) if part)
    return name or None


def _dedup_fallback(payload: dict[str, Any]) -> str:
    return payload_hash(payload)[:32]


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
