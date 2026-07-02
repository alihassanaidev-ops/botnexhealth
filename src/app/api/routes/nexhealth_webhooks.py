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

# Only these events trigger workflow enrollment.
_TRIGGER_EVENTS = frozenset({"appointment.created", "appointment.updated"})


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
    if event not in _TRIGGER_EVENTS:
        logger.debug("nexhealth_appointment_webhook: ignoring event=%s", event)
        return {"status": "ignored", "event": event}

    appt: dict = (payload.get("data") or {}).get("appointment") or {}
    nexhealth_location_id = str(appt.get("location_id", ""))
    appointment_id = str(appt.get("id", ""))
    start_time: str | None = appt.get("start_time")
    nexhealth_patient_id: str | None = (
        str(appt["patient_id"]) if appt.get("patient_id") else None
    )

    if not nexhealth_location_id or not appointment_id or not start_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Appointment payload missing required fields: location_id, id, or start_time",
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
        if nexhealth_patient_id:
            contact_row = await session.execute(
                select(Contact).where(
                    Contact.institution_id == institution_id,
                    Contact.nexhealth_patient_id == nexhealth_patient_id,
                )
            )
            contact = contact_row.scalar_one_or_none()
            if contact:
                contact_id = str(contact.id)

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
