"""NexHealth outbound-webhook receiver for appointment events."""

from __future__ import annotations

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
_ENROLL_EVENTS = frozenset({"appointment.created", "appointment.updated"})
_CANCEL_EVENTS = frozenset({"appointment.cancelled", "appointment.deleted"})
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


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """Raise 403 if HMAC-SHA256 signature does not match.

    When nexhealth_webhook_secret is empty (local dev / test) verification is
    skipped entirely — gate the endpoint at the network/firewall level instead.
    """
    secret = settings.nexhealth_webhook_secret
    if not secret:
        return
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-NexHealth-Signature header",
        )
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
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
    _verify_signature(raw_body, request.headers.get("X-NexHealth-Signature"))

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload"
        )

    event: str = payload.get("event", "")
    if event not in _HANDLED_EVENTS:
        logger.debug("nexhealth_appointment_webhook: ignoring event=%s", event)
        return {"status": "ignored", "event": event}

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

    # Cancellation path: terminate any already-scheduled runs/timers for this
    # appointment instead of enrolling. (Reschedules keep their enrollment; the
    # dispatch-time PmsLiveRevalidationService is the backstop that skips a send
    # whose appointment time no longer matches at fire time.)
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

    from src.app.tasks.automation_workflow import trigger_appointment_workflows

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

    logger.info(
        "nexhealth_appointment_webhook: queued trigger institution=%s appt=%s event=%s contact=%s",
        institution_id,
        appointment_id,
        event,
        contact_id or "none",
    )
    return {
        "status": "queued",
        "appointment_id": appointment_id,
        "institution_id": institution_id,
    }
