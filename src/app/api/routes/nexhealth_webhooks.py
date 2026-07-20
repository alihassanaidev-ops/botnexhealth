"""NexHealth outbound-webhook receiver for campaign data events."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from src.app.config import settings
from src.app.database import get_system_db_session
from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.services.dead_letter import capture_dead_letter
from src.app.services.sms_privacy import payload_hash, safe_error_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nexhealth/webhooks", tags=["NexHealth Webhooks"])

# Events we act on. Cancellations arrive as ``appointment_updated`` carrying
# ``cancelled: true`` — status is evaluated on every update, not just the
# cancellation event (Plan 09 §Technical Considerations). NexHealth also sends
# ``appointment_created`` when it detects a new appointment in the health record
# system; that payload uses ``data.appointments[]`` instead of
# ``data.appointment``.
# Values may carry a `.complete` suffix (e.g. "appointment_insertion.complete") — normalized to base.
_ENROLL_EVENTS = frozenset(
    {"appointment_insertion", "appointment_created", "appointment_updated"}
)
_PATIENT_EVENTS = frozenset({"patient_created", "patient_updated"})
_SYNC_STATUS_EVENTS = frozenset({"sync_status_read_change", "sync_status_write_change"})
_CANCEL_EVENTS: frozenset[str] = frozenset()  # no distinct cancel event; derived from the `cancelled` flag
_HANDLED_EVENTS = _ENROLL_EVENTS | _CANCEL_EVENTS | _PATIENT_EVENTS | _SYNC_STATUS_EVENTS


def _raw_payload_text(raw_body: bytes) -> str:
    return raw_body.decode("utf-8", errors="replace")


def _source_event_id(payload: dict[str, Any]) -> str | None:
    for key in ("id", "event_id", "webhook_event_id", "delivery_id"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("event_id", "webhook_event_id", "delivery_id"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _dedup_fallback(payload: dict[str, Any]) -> str:
    return payload_hash(payload)[:32]


def _appointment_is_cancelled(event: str, appt: dict) -> bool:
    """Whether this event represents a cancelled appointment."""
    if event in _CANCEL_EVENTS:
        return True
    return bool(appt.get("cancelled", False) or appt.get("canceled", False))


def _appointment_payloads(payload: dict[str, Any], event: str) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []

    appointment = data.get("appointment")
    if isinstance(appointment, dict):
        return [appointment]

    # NexHealth's documented appointment_created payload is plural:
    # {"data": {"appointments": [{...}]}}.
    appointments = data.get("appointments")
    if isinstance(appointments, list):
        return [appt for appt in appointments if isinstance(appt, dict)]

    # Keep the event in the signature for future event-specific shapes and to
    # make call sites read naturally.
    _ = event
    return []


def _patient_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []

    patient = data.get("patient") or data.get("user")
    if isinstance(patient, dict):
        return [patient]

    patients = data.get("patients") or data.get("users")
    if isinstance(patients, list):
        return [item for item in patients if isinstance(item, dict)]

    if data.get("id") is not None:
        return [data]
    return []


def _patient_location_ids(patient: dict[str, Any]) -> list[str]:
    values = patient.get("location_ids") or patient.get("locations") or []
    if isinstance(values, list):
        ids = []
        for value in values:
            if isinstance(value, dict):
                value = value.get("id")
            if value not in (None, ""):
                ids.append(str(value))
        return ids
    value = patient.get("location_id")
    return [str(value)] if value not in (None, "") else []


async def _cancel_runs_for_appointment(
    institution_id: str, appointment_id: str, *, reason: str
) -> int:
    """Cancel active workflow runs + their pending timers for an appointment.

    Used when an appointment is cancelled: any reminder/confirmation run already
    materialised for it is terminated and its scheduled timers cancelled so no
    send fires for a dead appointment. Returns the number of runs cancelled.
    """
    from src.app.models.automation_workflow import AutomationRunStatus, AutomationWorkflowRun
    from src.app.services.automation.enrollment_service import (
        AutomationWorkflowEnrollmentService,
    )
    from src.app.services.automation.scheduler_service import (
        AutomationWorkflowSchedulerService,
    )

    cancelled = 0
    async with get_system_db_session(
        "nexhealth_webhooks", institution_id=institution_id, external_id=appointment_id
    ) as session:
        result = await session.execute(
            select(AutomationWorkflowRun).where(
                AutomationWorkflowRun.institution_id == institution_id,
                AutomationWorkflowRun.trigger_ref_type == "appointment",
                AutomationWorkflowRun.trigger_ref_id == appointment_id,
                AutomationWorkflowRun.status.in_([
                    AutomationRunStatus.PENDING.value,
                    AutomationRunStatus.RUNNING.value,
                    AutomationRunStatus.WAITING.value,
                ]),
            )
        )
        runs = list(result.scalars().all())
        enroll_svc = AutomationWorkflowEnrollmentService(session)
        scheduler = AutomationWorkflowSchedulerService(session)
        for run in runs:
            await scheduler.cancel_timers_for_run(str(run.id))
            await enroll_svc.cancel_run(run, reason=reason)
            cancelled += 1
        await session.commit()
    return cancelled


def _verify_signature(
    raw_body: bytes,
    signature_header: str | None,
    timestamp_header: str | None,
) -> None:
    """Raise 403 if HMAC-SHA256 signature does not match.

    When nexhealth_webhook_secret is empty verification is skipped — this is
    permitted only in local/test, where the endpoint is firewalled. Production
    startup already fails closed if the secret is unset (see config.py), but we
    defend in depth here too: in production an unset secret rejects the request
    rather than accepting an unauthenticated, potentially cross-tenant enroll.
    """
    secret = settings.nexhealth_webhook_secret
    if not secret:
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Webhook signature secret is not configured",
            )
        return
    if not signature_header or not timestamp_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing NexHealth signature/timestamp headers",
        )
    # NexHealth signs `{timestamp}.{base64(raw_body)}` with HMAC-SHA256 using the endpoint
    # secret_key (verified live/docs 2026-07-14). The `timestamp` header value is used verbatim.
    signed = f"{timestamp_header}.{base64.b64encode(raw_body).decode('ascii')}"
    expected = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header.strip()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature",
        )


@router.post("/appointments", status_code=status.HTTP_200_OK)
async def nexhealth_appointment_webhook(request: Request) -> dict[str, Any]:
    """Handle NexHealth appointment.created and appointment.updated events.

    Verifies the request signature, resolves the internal institution and
    location from NexHealth's location_id, optionally resolves the contact
    from NexHealth's patient_id, then enqueues trigger_appointment_workflows
    so the engine can schedule enrollment for matching active workflows.

    Always returns 200 — NexHealth deactivates endpoints that return non-2xx
    consistently, so errors that should not cause retry are surfaced as 200
    with ``"status": "ignored"`` bodies.
    """
    raw_body = await request.body()
    _verify_signature(
        raw_body,
        request.headers.get("signature") or request.headers.get("X-NexHealth-Signature"),
        request.headers.get("timestamp"),
    )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload"
        )
    raw_payload = _raw_payload_text(raw_body)

    # NexHealth uses `event_name` (e.g. "appointment_insertion.complete"); normalize to the base
    # token. Fall back to `event` for backward compatibility with older/synthetic payloads.
    event_name: str = payload.get("event_name") or payload.get("event") or ""
    event: str = event_name.split(".", 1)[0]
    if event not in _HANDLED_EVENTS:
        logger.debug("nexhealth_appointment_webhook: ignoring event=%s", event_name)
        return {"status": "ignored", "event": event_name}
    if event in _SYNC_STATUS_EVENTS:
        return await _process_sync_status_webhook_payload(
            event=event,
            payload=payload,
            raw_payload=raw_payload,
        )
    if event in _PATIENT_EVENTS:
        return await _process_patient_webhook_payload(
            event=event,
            payload=payload,
            raw_payload=raw_payload,
        )

    appointments = _appointment_payloads(payload, event)
    if event == "appointment_created" and len(appointments) > 1:
        results = []
        for appt in appointments:
            results.append(
                await _process_appointment_event(
                    event=event,
                    appt=appt,
                    payload=payload,
                    raw_payload=raw_payload,
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

    appt = appointments[0] if appointments else {}
    return await _process_appointment_event(
        event=event,
        appt=appt,
        payload=payload,
        raw_payload=raw_payload,
    )


@router.post("/patients", status_code=status.HTTP_200_OK)
async def nexhealth_patient_webhook(request: Request) -> dict[str, Any]:
    """Handle NexHealth patient.created and patient.updated events."""
    raw_body = await request.body()
    _verify_signature(
        raw_body,
        request.headers.get("signature") or request.headers.get("X-NexHealth-Signature"),
        request.headers.get("timestamp"),
    )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload"
        )
    raw_payload = _raw_payload_text(raw_body)

    event_name: str = payload.get("event_name") or payload.get("event") or ""
    event: str = event_name.split(".", 1)[0]
    if event not in _PATIENT_EVENTS:
        logger.debug("nexhealth_patient_webhook: ignoring event=%s", event_name)
        return {"status": "ignored", "event": event_name}
    return await _process_patient_webhook_payload(
        event=event,
        payload=payload,
        raw_payload=raw_payload,
    )


@router.post("/sync-status", status_code=status.HTTP_200_OK)
async def nexhealth_sync_status_webhook(request: Request) -> dict[str, Any]:
    """Handle NexHealth sync-status read/write recovery events."""
    raw_body = await request.body()
    _verify_signature(
        raw_body,
        request.headers.get("signature") or request.headers.get("X-NexHealth-Signature"),
        request.headers.get("timestamp"),
    )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload"
        )
    raw_payload = _raw_payload_text(raw_body)

    event_name: str = payload.get("event_name") or payload.get("event") or ""
    event: str = event_name.split(".", 1)[0]
    if event not in _SYNC_STATUS_EVENTS:
        logger.debug("nexhealth_sync_status_webhook: ignoring event=%s", event_name)
        return {"status": "ignored", "event": event_name}
    return await _process_sync_status_webhook_payload(
        event=event,
        payload=payload,
        raw_payload=raw_payload,
    )


async def _process_sync_status_webhook_payload(
    *, event: str, payload: dict[str, Any], raw_payload: str | None = None
) -> dict[str, Any]:
    subdomain = str(payload.get("subdomain") or "")
    if not subdomain:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sync-status payload missing required field: subdomain",
        )

    from src.app.services.automation.nexhealth_projection_service import (
        NexHealthProjectionService,
    )
    from src.app.services.automation.nexhealth_subscription_service import (
        NexHealthSubscriptionLifecycleService,
    )
    from src.app.services.automation.nexhealth_sync_status_service import (
        NexHealthSyncStatusService,
    )

    async with get_system_db_session(
        "nexhealth_lookup", external_id=f"sync_status:{subdomain}"
    ) as session:
        sync_svc = NexHealthSyncStatusService(session)
        locations = await sync_svc.resolve_locations_for_payload(
            subdomain=subdomain,
            payload=payload,
        )

    if not locations:
        logger.warning(
            "nexhealth_sync_status_webhook: unknown subdomain=%s event=%s — skipping",
            subdomain,
            event,
        )
        return {"status": "ignored", "reason": "unknown_location", "event": event}

    institution_id = str(locations[0].institution_id)
    locations = [loc for loc in locations if str(loc.institution_id) == institution_id]
    local_location_ids = [str(loc.id) for loc in locations]
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    dedup_basis = (
        data.get("read_status_at")
        or data.get("write_status_at")
        or payload.get("event_time")
        or _dedup_fallback(payload)
    )
    dedup_key = f"{event}:{subdomain}:{','.join(local_location_ids)}:{dedup_basis}"

    async with get_system_db_session(
        "nexhealth_webhooks", institution_id=institution_id, external_id=dedup_key
    ) as session:
        lifecycle = NexHealthSubscriptionLifecycleService(session)
        for location_id in local_location_ids:
            await lifecycle.record_event_seen(
                institution_id=institution_id,
                location_id=location_id,
            )
        proj = NexHealthProjectionService(session)
        claimed = await proj.claim_event(
            institution_id=institution_id,
            event_type=event,
            dedup_key=dedup_key,
            source_event_id=_source_event_id(payload),
            payload=payload,
            raw_payload=raw_payload,
        )
        if not claimed:
            await session.commit()
            return {"status": "duplicate", "event": event}

        try:
            updated = await NexHealthSyncStatusService(session).upsert_for_locations(
                event=event,
                subdomain=subdomain,
                locations=locations,
                payload=payload,
            )
        except Exception as exc:  # noqa: BLE001 - valid webhooks are DLQ'd, not retried by NexHealth.
            return await _dead_letter_claimed_webhook(
                session=session,
                projection=proj,
                institution_id=institution_id,
                location_id=local_location_ids[0] if local_location_ids else None,
                dedup_key=dedup_key,
                event=event,
                payload=payload,
                raw_payload=raw_payload,
                error=exc,
            )
        await proj.complete_event(institution_id=institution_id, dedup_key=dedup_key)
        await session.commit()

    logger.info(
        "nexhealth_sync_status_webhook: refreshed institution=%s locations=%d event=%s",
        institution_id,
        updated,
        event,
    )
    return {
        "status": "processed",
        "event": event,
        "processed": updated,
        "institution_id": institution_id,
        "location_ids": local_location_ids,
    }


async def _dead_letter_claimed_webhook(
    *,
    session,
    projection,
    institution_id: str,
    location_id: str | None,
    dedup_key: str,
    event: str,
    payload: dict[str, Any],
    raw_payload: str | None,
    error: Exception,
) -> dict[str, Any]:
    row = await projection.complete_event(
        institution_id=institution_id,
        dedup_key=dedup_key,
        error=str(error),
    )
    await session.commit()
    await capture_dead_letter(
        source="nexhealth_webhook",
        event_type=event,
        error=error,
        payload=payload,
        raw_payload=raw_payload,
        attempts=row.attempts if row is not None else 1,
        institution_id=institution_id,
        location_id=location_id,
    )
    logger.warning(
        "nexhealth_webhook: dead-lettered event=%s institution=%s location=%s dedup_hash=%s error=%s",
        event,
        institution_id,
        location_id or "none",
        _dedup_fallback({"dedup_key": dedup_key}),
        safe_error_summary(error),
    )
    return {
        "status": "failed",
        "event": event,
        "dead_lettered": True,
        "institution_id": institution_id,
        "location_id": location_id,
    }


async def _process_patient_webhook_payload(
    *, event: str, payload: dict[str, Any], raw_payload: str | None = None
) -> dict[str, Any]:
    patients = _patient_payloads(payload)
    if not patients:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient payload missing required patient object",
        )

    results = [
        await _process_patient_event(
            event=event,
            patient=patient,
            payload=payload,
            raw_payload=raw_payload,
            subdomain=str(payload.get("subdomain") or ""),
            event_time=str(payload.get("event_time") or ""),
        )
        for patient in patients
    ]
    processed = sum(1 for result in results if result.get("status") not in {"ignored"})
    return {
        "status": "processed" if processed else "ignored",
        "event": event,
        "processed": processed,
        "results": results,
    }


async def _process_patient_event(
    *,
    event: str,
    patient: dict[str, Any],
    payload: dict[str, Any],
    raw_payload: str | None,
    subdomain: str,
    event_time: str,
) -> dict[str, Any]:
    patient_id = str(patient.get("id") or "")
    if not patient_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient payload missing required field: id",
        )

    nexhealth_location_ids = _patient_location_ids(patient)
    if not nexhealth_location_ids:
        logger.warning(
            "nexhealth_patient_webhook: patient=%s missing location_ids — skipping",
            patient_id,
        )
        return {"status": "ignored", "reason": "missing_location_ids", "patient_id": patient_id}

    async with get_system_db_session(
        "nexhealth_lookup", external_id=patient_id
    ) as session:
        stmt = select(InstitutionLocation).where(
            InstitutionLocation.nexhealth_location_id.in_(nexhealth_location_ids)
        )
        if subdomain:
            stmt = stmt.where(InstitutionLocation.nexhealth_subdomain == subdomain)
        loc_rows = await session.execute(stmt)
        locations = list(loc_rows.scalars().all())

    if not locations:
        logger.warning(
            "nexhealth_patient_webhook: unknown location_ids=%s patient=%s — skipping",
            nexhealth_location_ids,
            patient_id,
        )
        return {"status": "ignored", "reason": "unknown_location", "patient_id": patient_id}

    institution_id = str(locations[0].institution_id)
    locations = [loc for loc in locations if str(loc.institution_id) == institution_id]
    local_location_ids = [str(loc.id) for loc in locations]
    dedup_basis = patient.get("updated_at") or event_time or _dedup_fallback(payload)
    dedup_key = f"{event}:{patient_id}:{dedup_basis}"

    from src.app.services.automation.nexhealth_projection_service import (
        NexHealthProjectionService,
    )
    from src.app.services.automation.nexhealth_subscription_service import (
        NexHealthSubscriptionLifecycleService,
    )

    async with get_system_db_session(
        "nexhealth_webhooks", institution_id=institution_id, external_id=patient_id
    ) as session:
        lifecycle = NexHealthSubscriptionLifecycleService(session)
        for location_id in local_location_ids:
            await lifecycle.record_event_seen(
                institution_id=institution_id,
                location_id=location_id,
            )
        proj = NexHealthProjectionService(session)
        claimed = await proj.claim_event(
            institution_id=institution_id,
            patient_id=patient_id,
            event_type=event,
            dedup_key=dedup_key,
            source_event_id=_source_event_id(payload),
            payload=payload,
            raw_payload=raw_payload,
        )
        if not claimed:
            await session.commit()
            logger.info(
                "nexhealth_patient_webhook: duplicate event institution=%s patient=%s dedup=%s",
                institution_id,
                patient_id,
                dedup_key,
            )
            return {"status": "duplicate", "patient_id": patient_id}

        try:
            upsert = await proj.upsert_patient(
                institution_id=institution_id,
                patient=patient,
                local_location_ids=local_location_ids,
                nexhealth_location_ids=nexhealth_location_ids,
                event=event,
            )
        except Exception as exc:  # noqa: BLE001 - valid webhooks are DLQ'd, not retried by NexHealth.
            return await _dead_letter_claimed_webhook(
                session=session,
                projection=proj,
                institution_id=institution_id,
                location_id=local_location_ids[0] if local_location_ids else None,
                dedup_key=dedup_key,
                event=event,
                payload=payload,
                raw_payload=raw_payload,
                error=exc,
            )
        await proj.complete_event(institution_id=institution_id, dedup_key=dedup_key)
        await session.commit()

    logger.info(
        "nexhealth_patient_webhook: refreshed institution=%s patient=%s contact=%s event=%s change=%s",
        institution_id,
        patient_id,
        upsert.contact.id,
        event,
        upsert.change,
    )
    return {
        "status": upsert.change,
        "patient_id": patient_id,
        "contact_id": str(upsert.contact.id),
        "institution_id": institution_id,
    }


async def _process_appointment_event(
    event: str,
    appt: dict[str, Any],
    *,
    payload: dict[str, Any],
    raw_payload: str | None,
) -> dict[str, Any]:
    nexhealth_location_id = str(appt.get("location_id", ""))
    appointment_id = str(appt.get("id", ""))
    start_time: str | None = appt.get("start_time")
    nexhealth_patient_id: str | None = (
        str(appt["patient_id"]) if appt.get("patient_id") else None
    )
    provider_id: str | None = str(appt["provider_id"]) if appt.get("provider_id") else None
    appointment_type_id: str | None = (
        str(appt["appointment_type_id"]) if appt.get("appointment_type_id") else None
    )
    is_cancelled = _appointment_is_cancelled(event, appt)

    # location_id + id are always required; start_time only for the enroll path
    # (a cancellation may omit it).
    if not nexhealth_location_id or not appointment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Appointment payload missing required fields: location_id or id",
        )
    if not is_cancelled and not start_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Appointment payload missing required field: start_time",
        )

    async with get_system_db_session(
        "nexhealth_lookup", external_id=appointment_id
    ) as session:
        loc_row = await session.execute(
            select(InstitutionLocation).where(
                InstitutionLocation.nexhealth_location_id == nexhealth_location_id
            )
        )
        location = loc_row.scalar_one_or_none()

        if location is None:
            logger.warning(
                "nexhealth_appointment_webhook: unknown nexhealth_location_id=%s appt=%s — skipping",
                nexhealth_location_id,
                appointment_id,
            )
            return {"status": "ignored", "reason": "unknown_location"}

        institution_id = str(location.institution_id)
        location_id = str(location.id)

        contact_id: str | None = None
        if not is_cancelled and nexhealth_patient_id:
            contact_row = await session.execute(
                select(Contact).where(
                    Contact.institution_id == institution_id,
                    Contact.nexhealth_patient_id == nexhealth_patient_id,
                )
            )
            contact = contact_row.scalar_one_or_none()
            if contact:
                contact_id = str(contact.id)

    # ── Event-ledger claim + projection upsert (Plan 09 D-1/D-2/D-4) ──
    # dedup_key is the event's semantic identity: a redelivery of the same logical
    # change collides (skipped); a genuine reschedule (new start_time) does not.
    dedup_key = f"{event}:{appointment_id}:{'cancelled' if is_cancelled else (start_time or 'none')}"

    from src.app.services.automation.nexhealth_projection_service import (
        NexHealthProjectionService,
    )
    from src.app.services.automation.nexhealth_subscription_service import (
        NexHealthSubscriptionLifecycleService,
    )

    async with get_system_db_session(
        "nexhealth_webhooks", institution_id=institution_id, external_id=appointment_id
    ) as session:
        await NexHealthSubscriptionLifecycleService(session).record_event_seen(
            institution_id=institution_id,
            location_id=location_id,
        )
        proj = NexHealthProjectionService(session)
        claimed = await proj.claim_event(
            institution_id=institution_id,
            appointment_id=appointment_id,
            event_type=event,
            dedup_key=dedup_key,
            source_event_id=_source_event_id(payload),
            payload=payload,
            raw_payload=raw_payload,
        )
        if not claimed:
            await session.commit()
            logger.info(
                "nexhealth_appointment_webhook: duplicate event institution=%s appt=%s dedup=%s",
                institution_id, appointment_id, dedup_key,
            )
            return {"status": "duplicate", "appointment_id": appointment_id}

        try:
            upsert = await proj.upsert_appointment(
                institution_id=institution_id,
                appointment_id=appointment_id,
                location_id=location_id,
                nexhealth_patient_id=nexhealth_patient_id,
                contact_id=contact_id,
                start_time=start_time,
                event=event,
                cancelled=is_cancelled,
                provider_id=provider_id,
                appointment_type_id=appointment_type_id,
            )
        except Exception as exc:  # noqa: BLE001 - valid webhooks are DLQ'd, not retried by NexHealth.
            return await _dead_letter_claimed_webhook(
                session=session,
                projection=proj,
                institution_id=institution_id,
                location_id=location_id,
                dedup_key=dedup_key,
                event=event,
                payload=payload,
                raw_payload=raw_payload,
                error=exc,
            )
        change = upsert.change
        await proj.complete_event(institution_id=institution_id, dedup_key=dedup_key)
        await session.commit()

    # Cancellation: terminate any already-scheduled runs/timers for this appointment.
    if is_cancelled:
        runs_cancelled = await _cancel_runs_for_appointment(
            institution_id, appointment_id, reason="appointment_cancelled"
        )
        logger.info(
            "nexhealth_appointment_webhook: cancelled institution=%s appt=%s runs=%d event=%s",
            institution_id, appointment_id, runs_cancelled, event,
        )
        return {
            "status": "cancelled",
            "appointment_id": appointment_id,
            "institution_id": institution_id,
            "runs_cancelled": runs_cancelled,
        }

    # Unchanged update (same start_time): projection refreshed for the freshness
    # window, but no new enrollment needed — the existing run stands.
    if change == "unchanged":
        logger.info(
            "nexhealth_appointment_webhook: unchanged institution=%s appt=%s", institution_id, appointment_id,
        )
        return {"status": "unchanged", "appointment_id": appointment_id}

    # Reschedule: the old run was enrolled for the previous time. Cancel it + its
    # timers, then re-trigger — the time-aware idempotency key enrolls fresh at the
    # new time (Plan 09 D-1: previously the send was silently dropped).
    runs_cancelled = 0
    if change == "rescheduled":
        runs_cancelled = await _cancel_runs_for_appointment(
            institution_id, appointment_id, reason="appointment_rescheduled"
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
            "nexhealth_appointment_id": appointment_id,
            "nexhealth_location_id": nexhealth_location_id,
        },
    )
    if contact_id:
        resume_reactivation_booking.delay(
            institution_id=institution_id,
            location_id=location_id,
            contact_id=contact_id,
            appointment_id=appointment_id,
        )

    logger.info(
        "nexhealth_appointment_webhook: queued trigger institution=%s appt=%s event=%s change=%s contact=%s reenrolled_after_cancel=%d",
        institution_id, appointment_id, event, change, contact_id or "none", runs_cancelled,
    )
    return {
        "status": "queued",
        "change": change,
        "appointment_id": appointment_id,
        "institution_id": institution_id,
    }
