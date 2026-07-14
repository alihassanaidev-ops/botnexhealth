"""NexHealth outbound-webhook receiver for appointment events."""

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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nexhealth/webhooks", tags=["NexHealth Webhooks"])

# Events we act on. Cancellations arrive both as a dedicated event and as an
# ``appointment.updated`` carrying ``cancelled: true`` — status is evaluated on
# every update, not just the cancellation event (Plan 09 §Technical Considerations).
# NexHealth sends an `event_name` (verified live/docs 2026-07-14) — only two appointment
# events exist: `appointment_insertion` (created) and `appointment_updated` (any change, incl.
# cancellations, which arrive as an update carrying `cancelled: true`, NOT a distinct event).
# Values may carry a `.complete` suffix (e.g. "appointment_insertion.complete") — normalized to base.
_ENROLL_EVENTS = frozenset({"appointment_insertion", "appointment_updated"})
_CANCEL_EVENTS: frozenset[str] = frozenset()  # no distinct cancel event; derived from the `cancelled` flag
_HANDLED_EVENTS = _ENROLL_EVENTS | _CANCEL_EVENTS


def _appointment_is_cancelled(event: str, appt: dict) -> bool:
    """Whether this event represents a cancelled appointment."""
    if event in _CANCEL_EVENTS:
        return True
    return bool(appt.get("cancelled", False) or appt.get("canceled", False))


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

    # NexHealth uses `event_name` (e.g. "appointment_insertion.complete"); normalize to the base
    # token. Fall back to `event` for backward compatibility with older/synthetic payloads.
    event_name: str = payload.get("event_name") or payload.get("event") or ""
    event: str = event_name.split(".", 1)[0]
    if event not in _HANDLED_EVENTS:
        logger.debug("nexhealth_appointment_webhook: ignoring event=%s", event_name)
        return {"status": "ignored", "event": event_name}

    appt: dict = (payload.get("data") or {}).get("appointment") or {}
    nexhealth_location_id = str(appt.get("location_id", ""))
    appointment_id = str(appt.get("id", ""))
    start_time: str | None = appt.get("start_time")
    nexhealth_patient_id: str | None = (
        str(appt["patient_id"]) if appt.get("patient_id") else None
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
        )
        if not claimed:
            await session.commit()
            logger.info(
                "nexhealth_appointment_webhook: duplicate event institution=%s appt=%s dedup=%s",
                institution_id, appointment_id, dedup_key,
            )
            return {"status": "duplicate", "appointment_id": appointment_id}

        upsert = await proj.upsert_appointment(
            institution_id=institution_id,
            appointment_id=appointment_id,
            location_id=location_id,
            nexhealth_patient_id=nexhealth_patient_id,
            contact_id=contact_id,
            start_time=start_time,
            event=event,
            cancelled=is_cancelled,
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
