"""Retell webhook handlers for call events (call_analyzed, call_ended).

We consume Retell's raw (unscrubbed) variants — transcript, recording URL,
and analysis — and persist them. Raw PHI therefore lives in our datastore;
it is protected by column-level encryption at rest plus RBAC/tenant-scoped
access rather than by scrubbing at the webhook boundary. Retell's ``scrubbed_*``
variants are also persisted (plaintext, non-PHI) and shown inline by default;
for the single raw fields the scrubbed value additionally serves as a fallback
when Retell omits the raw one.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.config import settings
from src.app.retell.security import (
    get_retell_secret,
    get_signature_dependency,
    hash_for_logging,
)
from src.app.services.sms_privacy import safe_error_summary, sanitize_provider_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retell", tags=["Retell Webhooks"])

# Create signature verification dependency
verify_webhook_signature = get_signature_dependency(get_retell_secret)


def _build_dashboard_link(call_row_id: str) -> str:
    base = (settings.auth_frontend_base_url or "").rstrip("/")
    if not base:
        return ""
    return f"{base}/calls?detail={call_row_id}"


def _nopms_call_tags(db_call) -> set[str]:  # noqa: ANN001
    """Normalized tag set for a call, including the primary status."""
    tags = {
        part.strip().lower()
        for part in (db_call.call_tags or "").split(",")
        if part.strip()
    }
    primary = (db_call.call_status or "").strip().lower()
    if primary:
        tags.add(primary)
    return tags


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return "unknown"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def _nopms_alert_variables(*, location_name: str, db_call) -> dict[str, str]:  # noqa: ANN001
    """Render context for the staff SMS templates.

    PHI-free by construction: triage metadata and a dashboard link only. These
    text the clinic's own staff numbers, so no patient name or DOB is exposed
    here — the variable set mirrors the staff email templates.
    """
    tags = _nopms_call_tags(db_call)
    status = (db_call.call_status or "").strip().lower()
    return {
        "location_name": location_name,
        "call_status": status.replace("_", " ").title() if status else "No Action Needed",
        "duration": _format_duration(db_call.call_duration_seconds),
        "sentiment": db_call.patient_sentiment or "Unknown",
        "availability": db_call.requested_availability or "Not provided",
        "new_patient": "Yes" if db_call.is_new_patient else "No",
        "emergency": "Yes" if "emergency" in tags else "No",
        "urgency": "Emergency" if "emergency" in tags else "Complaint",
        "dashboard_link": _build_dashboard_link(str(db_call.id)) or "",
    }


def _nopms_applicable_alerts(db_call) -> list[str]:  # noqa: ANN001
    """Which staff alert types this call triggers.

    Appointment requests need a needs_booking classification, the urgent alert
    fires on emergency/complaint, and the call summary fires on every call.
    Whether a given number is subscribed to a type is a separate check.
    """
    from src.app.models.call import CallStatus
    from src.app.models.external_sms_notification_recipient import StaffSmsAlertType

    tags = _nopms_call_tags(db_call)
    applicable: list[str] = []
    if CallStatus.NEEDS_BOOKING.value in tags:
        applicable.append(StaffSmsAlertType.APPOINTMENT_REQUEST.value)
    if tags & {"emergency", "complaint"}:
        applicable.append(StaffSmsAlertType.URGENT_ALERT.value)
    applicable.append(StaffSmsAlertType.CALL_SUMMARY.value)
    return applicable



async def _queue_nopms_staff_sms(session, *, institution, location, db_call) -> int:  # noqa: ANN001
    """Fan the staff SMS alerts out to their subscribers. Returns the count queued.

    Runs on ``call_analyzed`` rather than ``call_ended`` because the alert set
    is derived from ``call_status`` / ``call_tags``, and those are written by
    the post-call pipeline this event drives. On ``call_ended`` the Call row
    does not exist yet, so every alert was silently skipped.
    """
    if institution.has_pms or location is None or not location.twilio_from_number:
        return 0

    from src.app.services.sms_notification_recipients import (
        resolve_sms_notification_recipients,
    )
    from src.app.services.sms_template_service import SmsTemplateService
    from src.app.tasks.sms import enqueue_auto_sms

    alert_vars = _nopms_alert_variables(location_name=location.name, db_call=db_call)
    sms_svc = SmsTemplateService(session)
    queued = 0

    for alert_type in _nopms_applicable_alerts(db_call):
        subscribed = await resolve_sms_notification_recipients(
            session,
            institution_id=str(institution.id),
            notification_type=alert_type,
            location_id=str(location.id),
        )
        if not subscribed:
            continue
        # Staff alert wording is an editable template, same as the staff
        # emails. An inactive template mutes that alert without unsubscribing
        # anyone.
        alert_template = await sms_svc.get_template_by_type(institution.id, alert_type)
        if not alert_template or not alert_template.is_active:
            logger.info(
                "Staff SMS skipped, template inactive: type=%s institution_hash=%s",
                alert_type,
                hash_for_logging(str(institution.id)),
            )
            continue
        body = SmsTemplateService.render(alert_template.body, alert_vars)
        for phone in subscribed:
            enqueue_auto_sms(
                from_number=location.twilio_from_number,
                to_number=phone,
                body=body,
                institution_location_id=str(location.id),
                patient_contact_id=None,
                call_id=str(db_call.id),
            )
            queued += 1

    logger.info(
        "Staff SMS alerts queued: call_hash=%s location_hash=%s queued=%d",
        hash_for_logging(str(db_call.id)),
        hash_for_logging(str(location.id)),
        queued,
    )
    return queued


# ============================================================================
# Pydantic Models for Retell call_analyzed webhook
# ============================================================================

class CallAnalysisData(BaseModel):
    """Analysis data extracted by Retell (raw; scrubbed variant used only as fallback)."""

    call_summary: str | None = Field(None, alias="call_summary")
    in_voicemail: bool | None = None
    user_sentiment: str | None = None
    call_successful: bool | None = None
    custom_analysis_data: dict[str, Any] = Field(default_factory=dict)


class RetellCallWebhook(BaseModel):
    """Call data from Retell webhook.

    The raw (unscrubbed) variants of recording, transcript, and analysis are
    consumed and persisted; the ``scrubbed_*`` variants are retained only as a
    fallback for when Retell omits the raw fields.
    """

    model_config = ConfigDict(extra="ignore")

    call_id: str
    call_type: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    call_status: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    direction: str | None = None
    duration_ms: int | None = None
    start_timestamp: int | None = None
    end_timestamp: int | None = None
    recording_url: str | None = None
    transcript_with_tool_calls: list[dict] | None = None
    call_analysis: CallAnalysisData | None = None
    scrubbed_recording_url: str | None = None
    scrubbed_transcript_with_tool_calls: list[dict] | None = None
    disconnection_reason: str | None = None
    scrubbed_call_analysis: CallAnalysisData | None = None
    # Dynamic variables collected during the call (name, email, etc.)
    collected_dynamic_variables: dict[str, Any] = Field(default_factory=dict)


class RetellWebhookEvent(BaseModel):
    """Retell webhook event envelope."""

    event: str
    call: RetellCallWebhook


class RetellAgentLookupError(RuntimeError):
    """Raised when a Retell agent lookup fails for retryable infrastructure reasons."""


async def _resolve_institution_location_from_agent(agent_id: str | None):
    """Resolve the active location/institution pair for a Retell agent.

    A missing or unmapped agent is treated as a configuration no-match. Lookup
    exceptions are different: those indicate DB/RLS/infrastructure failure and
    must be allowed to fail the webhook so idempotency stays retryable.
    """
    if not agent_id:
        logger.warning("Retell webhook missing agent_id; call will not be persisted")
        return None, None

    try:
        from src.app.database import get_system_db_session
        from src.app.services.institution_service import InstitutionService

        async with get_system_db_session(
            "retell_lookup",
            external_id=agent_id,
        ) as session:
            institution_service = InstitutionService(session)
            result = await institution_service.get_location_by_retell_agent_id(agent_id)
            if result:
                location, institution = result
                return location, institution
    except Exception as exc:
        logger.error(
            "Retell agent lookup failed; webhook will be marked retryable: agent_hash=%s error=%s",
            hash_for_logging(agent_id),
            safe_error_summary(exc),
        )
        raise RetellAgentLookupError(
            "Retell agent lookup failed; retry webhook"
        ) from exc

    logger.warning(
        "Retell webhook agent has no active location mapping; call will not be persisted: agent=%s",
        hash_for_logging(agent_id),
    )
    return None, None


async def _begin_webhook_processing(call_id: str, event_type: str) -> tuple[bool, str]:
    """Create or claim idempotency record for a webhook event.

    Commits the row before returning so a downstream failure can never leave
    the claim in an uncommitted state. Without the explicit commit a later
    rollback would erase the PROCESSING marker and the next retry would
    re-execute the side-effect.

    Returns:
        (can_process, reason)
    """
    from src.app.database import get_system_db_session
    from src.app.models.retell_webhook_event import (
        RetellWebhookEvent,
        RetellWebhookStatus,
    )

    async with get_system_db_session("retell", external_id=call_id) as session:
        existing = (
            await session.execute(
                select(RetellWebhookEvent).where(
                    RetellWebhookEvent.call_id == call_id,
                    RetellWebhookEvent.event_type == event_type,
                )
            )
        ).scalar_one_or_none()

        if existing:
            if existing.status == RetellWebhookStatus.COMPLETED.value:
                return False, "already_completed"
            if existing.status == RetellWebhookStatus.PROCESSING.value:
                return False, "already_processing"

            # Retry a previously failed event.
            existing.status = RetellWebhookStatus.PROCESSING.value
            existing.attempts += 1
            existing.last_error = None
            existing.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return True, "retry_failed_event"

        event = RetellWebhookEvent(
            call_id=call_id,
            event_type=event_type,
            status=RetellWebhookStatus.PROCESSING.value,
            attempts=1,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(event)
        try:
            await session.flush()
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False, "already_processing"
        return True, "new_event"


async def _finish_webhook_processing(
    call_id: str,
    event_type: str,
    *,
    status: str,
    institution_id: str | None = None,
    error: str | None = None,
) -> None:
    """Update idempotency row after webhook processing finishes.

    Commits explicitly so terminal status is durable even if the surrounding
    request handler raises after this call.
    """
    from src.app.database import get_system_db_session
    from src.app.models.retell_webhook_event import RetellWebhookEvent

    async with get_system_db_session(
        "retell",
        institution_id=institution_id,
        external_id=call_id,
    ) as session:
        row = (
            await session.execute(
                select(RetellWebhookEvent).where(
                    RetellWebhookEvent.call_id == call_id,
                    RetellWebhookEvent.event_type == event_type,
                )
            )
        ).scalar_one_or_none()

        if not row:
            return
        row.status = status
        row.institution_id = institution_id or row.institution_id
        row.last_error = error
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()


async def process_retell_call_ended_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Send the patient the appointment-confirmation SMS at call end.

    Approach B: the body is rendered from the institution's editable
    ``appointment_booked`` SMS template, populated with the authoritative PMS
    booking (real provider name + slot time) resolved from the ``book_appointment``
    invocation — not Retell's free-text message. Only fires when an appointment
    was actually booked during the call. Consent, suppression, clinic identity,
    and the CASL/TCPA footer are enforced downstream by the SMS pipeline.
    """
    event = RetellWebhookEvent.model_validate(payload)
    call = event.call
    call_id = call.call_id

    async def _finish(
        status: str, institution_id: str | None = None, error: str | None = None
    ) -> None:
        try:
            await _finish_webhook_processing(
                call_id,
                "call_ended",
                status=status,
                institution_id=institution_id,
                error=error,
            )
        except Exception as exc:  # never mask the real outcome on a finalize hiccup
            logger.warning(
                "Failed to finalize call_ended idempotency: call_hash=%s error=%s",
                hash_for_logging(call_id),
                safe_error_summary(exc),
            )

    # Agent-lookup infra failures must stay retryable (raise); a missing mapping
    # is a terminal no-op.
    location, institution = await _resolve_institution_location_from_agent(
        call.agent_id
    )
    if not location or not institution:
        await _finish("COMPLETED")
        return {"status": "ignored", "reason": "no_agent_mapping"}

    from src.app.database import get_system_db_session
    from src.app.models.sms_template import SmsTemplateType
    from src.app.services.appointment_context import resolve_booking_context
    from src.app.services.sms_template_service import SmsTemplateService

    body: str | None = None
    patient_phone: str | None = None
    db_call_for_sms = None
    skip_reason: str | None = None

    try:
        async with get_system_db_session(
            "retell",
            institution_id=institution.id,
            location_id=str(location.id),
            external_id=call_id,
        ) as session:
            if not institution.has_pms:
                # No-PMS: nothing is ever booked, so send a "request received"
                # acknowledgement instead of a confirmation — but only for calls
                # the agent classified as a booking request (needs_booking),
                # never every caller. Requires the call_analyzed classification
                # to have landed (it writes call.call_status); if it hasn't yet,
                # we skip rather than risk texting the wrong caller.
                from src.app.models.call import Call, CallStatus

                db_call = (
                    await session.execute(
                        select(Call).where(
                            Call.retell_call_id == call_id,
                            Call.institution_id == institution.id,
                        )
                    )
                ).scalar_one_or_none()
                if (
                    db_call is None
                    or (db_call.call_status or "") != CallStatus.NEEDS_BOOKING.value
                ):
                    skip_reason = "not_booking_request"
                elif not location.twilio_from_number:
                    skip_reason = "no_twilio_number"
                else:
                    patient_phone = (
                        call.to_number
                        if call.direction == "outbound"
                        else (call.from_number or call.to_number)
                    )
                    if not patient_phone:
                        skip_reason = "no_patient_phone"
                    else:
                        template = await SmsTemplateService(session).get_template_by_type(
                            institution.id, SmsTemplateType.APPOINTMENT_REQUEST.value
                        )
                        if not template or not template.is_active:
                            skip_reason = "template_inactive"
                        else:
                            dyn = call.collected_dynamic_variables or {}
                            # Patient's own phone → addressing them by name is
                            # fine; fall back to a neutral greeting when absent.
                            patient_name = (
                                dyn.get("patient_name") or dyn.get("name") or "there"
                            )
                            body = SmsTemplateService.render(
                                template.body,
                                {
                                    "patient_name": patient_name,
                                    "location_name": location.name,
                                    "availability": "",
                                },
                            )
                # No PMS booking path for these tenants — fall through to the
                # shared send/skip logic below with whatever was resolved above.
                booking = None
            else:
                booking = await resolve_booking_context(
                    session,
                    institution_id=institution.id,
                    retell_call_id=call_id,
                    timezone=location.timezone or "UTC",
                )
                if not booking or not booking.booked:
                    skip_reason = "no_booking"
                elif not location.twilio_from_number:
                    skip_reason = "no_twilio_number"
                else:
                    if call.direction == "outbound":
                        patient_phone = call.to_number
                    else:
                        patient_phone = call.from_number or call.to_number

                    if not patient_phone:
                        skip_reason = "no_patient_phone"
                    else:
                        template = await SmsTemplateService(session).get_template_by_type(
                            institution.id, SmsTemplateType.APPOINTMENT_BOOKED.value
                        )
                        if not template or not template.is_active:
                            skip_reason = "template_inactive"
                        else:
                            dyn = call.collected_dynamic_variables or {}
                            patient_name = dyn.get("patient_name") or dyn.get("name")
                            if not patient_name and booking.patient_id:
                                # Retell's collected_dynamic_variables are unreliable
                                # in webhook payloads (often omitted), so fall back to
                                # the PMS patient name — authoritative, collected at
                                # intake — the same source the confirmation email uses.
                                try:
                                    from src.app.pms.nexhealth.adapter import (
                                        NexHealthAdapter,
                                    )

                                    _adapter = await NexHealthAdapter.create(
                                        institution, location
                                    )
                                    _patient = await _adapter.get_patient(
                                        booking.patient_id
                                    )
                                    if _patient:
                                        patient_name = _patient.name or " ".join(
                                            p
                                            for p in (
                                                _patient.first_name,
                                                _patient.last_name,
                                            )
                                            if p
                                        ).strip()
                                except Exception as _name_err:
                                    logger.warning(
                                        "PMS patient-name lookup failed: call_hash=%s error=%s",
                                        hash_for_logging(call_id),
                                        safe_error_summary(_name_err),
                                    )
                            patient_name = patient_name or "there"
                            body = SmsTemplateService.render(
                                template.body,
                                {
                                    "patient_name": patient_name,
                                    "location_name": location.name,
                                    "appointment_provider": booking.provider_name
                                    or "your provider",
                                    "appointment_datetime": booking.appointment_datetime
                                    or "your scheduled time",
                                    "appointment_service": booking.service or "",
                                },
                            )
    except Exception as exc:
        await _finish(
            "FAILED", institution_id=str(institution.id), error=safe_error_summary(exc)
        )
        raise

    if institution.has_pms and (skip_reason or not body or not patient_phone):
        await _finish("COMPLETED", institution_id=str(institution.id))
        logger.info(
            "call_ended confirmation SMS skipped: call_hash=%s reason=%s",
            hash_for_logging(call_id),
            skip_reason or "no_body",
        )
        return {"status": "skipped", "reason": skip_reason or "no_body"}

    try:
        from src.app.tasks.sms import enqueue_auto_sms

        queued_patient = False
        if body and patient_phone and not skip_reason:
            enqueue_auto_sms(
                from_number=location.twilio_from_number,  # type: ignore[arg-type]
                to_number=patient_phone,
                body=body,
                institution_location_id=str(location.id),
                patient_contact_id=(
                    str(db_call_for_sms.contact_id)
                    if db_call_for_sms is not None and db_call_for_sms.contact_id
                    else None
                ),
                call_id=(
                    str(db_call_for_sms.id)
                    if db_call_for_sms is not None
                    else None
                ),
            )
            queued_patient = True

    except Exception as exc:
        await _finish(
            "FAILED", institution_id=str(institution.id), error=safe_error_summary(exc)
        )
        raise

    # Staff alerts are dispatched from call_analyzed, so this only reports
    # the patient acknowledgement.
    if not queued_patient:
        await _finish("COMPLETED", institution_id=str(institution.id))
        logger.info(
            "call_ended confirmation SMS skipped: call_hash=%s reason=%s",
            hash_for_logging(call_id),
            skip_reason or "no_body",
        )
        return {"status": "skipped", "reason": skip_reason or "no_body"}

    await _finish("COMPLETED", institution_id=str(institution.id))
    logger.info(
        "call_ended confirmation SMS enqueued: call_hash=%s to_hash=%s location_hash=%s",
        hash_for_logging(call_id),
        hash_for_logging(patient_phone),
        hash_for_logging(str(location.id)),
    )
    return {"status": "queued", "call_id": hash_for_logging(call_id)}


# ============================================================================
# Webhook Endpoint
# ============================================================================


@router.post("/webhook")
async def handle_retell_webhook(
    body: bytes = Depends(verify_webhook_signature),
) -> dict[str, str]:
    """Async-handoff Retell webhook endpoint.

    The hot path here is intentionally tiny — verify, parse, claim
    idempotency, enqueue, return. The actual call-analysis pipeline
    (institution resolution, ``PostCallService`` writes, downstream
    notifications, audit row) lives in
    :func:`process_retell_call_analyzed_event` and runs on a Celery
    worker via ``src.app.tasks.webhooks.process_retell_call_analyzed``.

    Why: a slow DB write or a NexHealth lookup hiccup must not back
    up the ALB request queue. Vendor retries are bounded; ours
    aren't. The async handoff keeps the handler p95 well under
    100ms regardless of downstream latency.

    Security: requires a valid Retell signature
    (``x-retell-signature`` header) — verified by the route dependency.
    """
    try:
        payload = json.loads(body)
        event = RetellWebhookEvent.model_validate(payload)
    except Exception as parse_err:
        logger.error("Retell webhook parse error: %s", safe_error_summary(parse_err))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Retell webhook payload",
        )

    logger.info(
        "Received Retell webhook: event=%s call_id_hash=%s",
        event.event,
        hash_for_logging(event.call.call_id),
    )

    # Drop event types we don't process before they take a queue slot.
    if event.event not in ("call_analyzed", "call_ended"):
        logger.info("Ignoring event type: %s", event.event)
        return {
            "status": "ignored",
            "reason": f"Event type {event.event} not processed",
        }

    # Idempotency claim happens HERE in the request thread so the
    # handler can return a deterministic ``duplicate`` response without
    # spending a worker round-trip. The claim is committed before this
    # call returns; if the task crashes mid-processing the row stays
    # PROCESSING and Celery's autoretry picks it back up.
    can_process, reason = await _begin_webhook_processing(
        event.call.call_id, event.event
    )
    if not can_process:
        logger.info(
            "Skipping duplicate Retell webhook: call_id_hash=%s event=%s reason=%s",
            hash_for_logging(event.call.call_id),
            event.event,
            reason,
        )
        return {"status": "duplicate", "reason": reason}

    # Hand off to the worker. The task runs on the dedicated ``webhooks``
    # queue so a backlog of call-analyzed events doesn't starve the
    # notifications/SMS queues for worker capacity.
    from src.app.tasks.webhooks import (
        process_retell_call_analyzed,
        process_retell_call_ended,
    )

    if event.event == "call_analyzed":
        process_retell_call_analyzed.delay(payload)
    else:
        process_retell_call_ended.delay(payload)

    return {
        "status": "queued",
        "event": event.event,
        "call_id": hash_for_logging(event.call.call_id),
    }


async def process_retell_call_analyzed_event(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Run the post-claim processing pipeline for a ``call_analyzed`` event.

    Designed to be called from the Celery task wrapper (which wraps it in
    ``asyncio.run``) AND directly from tests. Mirrors the legacy inline
    behaviour exactly:

      1. Resolve institution + location from ``agent_id``.
      2. ``PostCallService`` writes the contact + call rows.
      3. Enqueue downstream tasks (recording upload, notification email,
         in-app notification, auto-SMS) — same pattern as before, the
         only change is that those enqueues now happen from a worker
         instead of a request handler.
      4. Mark the idempotency row COMPLETED and write the SUCCESS audit.

    On any exception: mark the idempotency row FAILED, write a FAILURE
    audit, capture in dead_letter_events, and re-raise so Celery's
    autoretry takes over (with backoff). After ``max_retries`` the task
    is dropped — the dead-letter row + FAILED idempotency status are
    the operator signal.

    Returns a small status dict suitable for the Celery task return
    value (visible in worker logs).
    """
    processing_call_id: str | None = None
    processing_event_type: str | None = None
    institution = None
    location = None

    try:
        event = RetellWebhookEvent.model_validate(payload)
        processing_call_id = event.call.call_id
        processing_event_type = event.event

        # Resolve institution + location from agent_id. A no-match is a
        # configuration no-op; lookup exceptions are retryable and must not be
        # converted into a COMPLETED idempotency row.
        location, institution = await _resolve_institution_location_from_agent(
            event.call.agent_id
        )

        # NOTE: the audit row for this webhook is written ONCE at the bottom
        # of this function — either the success path (after processing
        # completes) or the except branch (with FAILURE_INTERNAL). Writing it
        # here too produced two rows per webhook on failure, which misled
        # downstream audit reports.

        # Process Contact & Call records if we identified the institution
        if institution:
            from src.app.database import get_system_db_session
            from src.app.services.post_call_service import PostCallService

            async with get_system_db_session(
                "retell",
                institution_id=institution.id,
                location_id=str(location.id) if location else None,
                external_id=event.call.call_id,
            ) as session:
                post_call_service = PostCallService(session)

                # Prefer Retell's raw (unscrubbed) analysis; fall back to the
                # scrubbed variant only when the raw field is absent.
                _analysis = (
                    event.call.call_analysis or event.call.scrubbed_call_analysis
                )
                analysis_dict = _analysis.model_dump() if _analysis else {}
                # Capture the scrubbed summary separately (non-PII) so it can be
                # stored alongside the raw summary and shown inline by default.
                _scrubbed_summary = (
                    event.call.scrubbed_call_analysis.call_summary
                    if event.call.scrubbed_call_analysis
                    else None
                )
                # Merge top-level collected_dynamic_variables so the service can use them
                # as a source for name/email when custom_analysis_data fields are missing.
                analysis_dict["collected_dynamic_variables"] = (
                    event.call.collected_dynamic_variables or {}
                )

                from src.app.retell.models import RetellCallData

                mapped_call_data = RetellCallData(
                    call_id=event.call.call_id,
                    call_type=event.call.call_type,
                    from_number=event.call.from_number,
                    to_number=event.call.to_number,
                    direction=event.call.direction,
                    agent_id=event.call.agent_id,
                    call_status=event.call.call_status,
                    disconnection_reason=event.call.disconnection_reason,
                    start_timestamp=event.call.start_timestamp,
                    end_timestamp=event.call.end_timestamp,
                    recording_url=(
                        event.call.recording_url or event.call.scrubbed_recording_url
                    ),
                    transcript_with_tool_calls=(
                        event.call.transcript_with_tool_calls
                        or event.call.scrubbed_transcript_with_tool_calls
                    ),
                    # Scrubbed variants stored as-is (no raw fallback): if Retell
                    # didn't send them, they stay NULL and the UI falls back to
                    # reveal-only rather than showing raw PHI unmasked.
                    scrubbed_recording_url=event.call.scrubbed_recording_url,
                    scrubbed_transcript_with_tool_calls=(
                        event.call.scrubbed_transcript_with_tool_calls
                    ),
                    scrubbed_summary=_scrubbed_summary,
                )

                # Call service to save to DB (analysis_dict is always a dict now)
                saved_call = await post_call_service.process_call_analyzed_event(
                    institution_id=institution.id,
                    location_id=str(location.id) if location else None,
                    webhook_call=mapped_call_data,
                    analysis=analysis_dict,
                    has_pms=institution.has_pms,
                )

                # Commit the transaction so contacts and calls are saved!
                await session.commit()

            # ── Recording upload: enqueue S3 upload after DB commit ──
            _rec_url = event.call.recording_url or event.call.scrubbed_recording_url
            if _rec_url:
                try:
                    from src.app.tasks.recordings import enqueue_recording_upload

                    enqueue_recording_upload(
                        call_id=saved_call.id,
                        institution_id=institution.id,
                        recording_url=_rec_url,
                    )
                except Exception as rec_enqueue_err:
                    logger.error(
                        "Failed to enqueue recording upload: call_hash=%s error=%s",
                        hash_for_logging(event.call.call_id),
                        safe_error_summary(rec_enqueue_err),
                    )

            # ── Email notification: enqueue after DB commit (durable via Celery) ──
            try:
                from src.app.tasks.notifications import enqueue_call_notification

                enqueue_call_notification(
                    call_id=saved_call.id,
                    institution_id=institution.id,
                    location_id=location.id if location else None,
                    call_status=saved_call.call_status,
                    call_tags_csv=saved_call.call_tags,
                    analysis_snapshot={
                        "custom_analysis_data": analysis_dict.get(
                            "custom_analysis_data"
                        )
                        or {},
                        "collected_dynamic_variables": analysis_dict.get(
                            "collected_dynamic_variables"
                        )
                        or {},
                    },
                )
            except Exception as email_enqueue_err:
                logger.error(
                    "Failed to enqueue call email notification: call_hash=%s error=%s",
                    hash_for_logging(event.call.call_id),
                    safe_error_summary(email_enqueue_err),
                )

            # ── Staff SMS alerts: same lifecycle point as the staff email,
            #    so the call classification they key off is already written. ──
            try:
                await _queue_nopms_staff_sms(
                    session,
                    institution=institution,
                    location=location,
                    db_call=saved_call,
                )
            except Exception as sms_enqueue_err:
                logger.error(
                    "Failed to enqueue staff SMS alerts: call_hash=%s error=%s",
                    hash_for_logging(event.call.call_id),
                    safe_error_summary(sms_enqueue_err),
                )

            # ── In-app notification: enqueue after DB commit (durable via Celery) ──
            try:
                from src.app.tasks.in_app_notifications import (
                    enqueue_in_app_notifications,
                )

                enqueue_in_app_notifications(
                    call_id=saved_call.id,
                    institution_id=institution.id,
                    location_id=location.id if location else None,
                    call_status=saved_call.call_status,
                    call_tags_csv=saved_call.call_tags,
                )
            except Exception as in_app_enqueue_err:
                logger.error(
                    "Failed to enqueue in-app notification: call_hash=%s error=%s",
                    hash_for_logging(event.call.call_id),
                    safe_error_summary(in_app_enqueue_err),
                )

            # Patient appointment-confirmation SMS is now sent on ``call_ended``
            # (Approach B — our own editable template populated from the
            # authoritative PMS booking), see ``process_retell_call_ended_event``.
            # The old Retell ``send_sms`` auto-SMS on call_analyzed was retired so
            # the patient isn't texted twice.

        await _finish_webhook_processing(
            processing_call_id,
            processing_event_type,
            status="COMPLETED",
            institution_id=institution.id if institution else None,
        )

        # Single audit row per webhook — written here, after processing has
        # actually completed. Failure path writes its own row in the except
        # branch below. We carry location_id forward because each Retell
        # agent maps 1:1 to a location, so call audits should be scoped at
        # that level (compliance reports filter by location).
        from src.app.services.audit import (
            log_audit_background,
            AuditAction,
            AuditActor,
            AuditOutcome,
        )

        log_audit_background(
            actor=AuditActor.RETELL_AGENT,
            action=AuditAction.WEBHOOK_RECEIVED,
            target_resource=f"call:{hash_for_logging(event.call.call_id)}",
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "event_type": event.event,
                "call_id": hash_for_logging(event.call.call_id),
            },
            institution_id=institution.id if institution else None,
            location_id=str(location.id) if location else None,
        )

        return {
            "status": "success",
            "event": event.event,
            "call_id": hash_for_logging(event.call.call_id),
        }

    except Exception as e:
        safe_error = sanitize_provider_error(e)
        logger.error("Retell call_analyzed processing error: %s", safe_error)

        # Audit webhook failure — include the institution_id (and call_id
        # hash) when we resolved them before the crash, so the failure row
        # is properly tenant-scoped for compliance reports.
        try:
            from src.app.services.audit import (
                AuditAction,
                AuditActor,
                AuditOutcome,
                log_audit_background,
            )

            failure_target = (
                f"call:{hash_for_logging(processing_call_id)}"
                if processing_call_id
                else "webhook:retell"
            )
            failure_metadata: dict[str, Any] = {"error": safe_error}
            if processing_event_type:
                failure_metadata["event_type"] = processing_event_type
            if processing_call_id:
                failure_metadata["call_id"] = hash_for_logging(processing_call_id)
            log_audit_background(
                actor=AuditActor.RETELL_AGENT,
                action=AuditAction.WEBHOOK_RECEIVED,
                target_resource=failure_target,
                outcome=AuditOutcome.FAILURE_INTERNAL,
                metadata=failure_metadata,
                institution_id=institution.id if institution else None,
                location_id=str(location.id) if location else None,
            )
        except Exception as audit_err:
            # Audit-write failure must not mask the original exception —
            # Celery's autoretry needs to see it.
            logger.warning(
                "Failed to write FAILURE_INTERNAL audit: %s",
                safe_error_summary(audit_err),
            )

        try:
            from src.app.services.dead_letter import capture_dead_letter

            await capture_dead_letter(
                source="retell_webhook",
                event_type=processing_event_type or "retell_webhook",
                error=safe_error,
                payload=payload,
                raw_payload=None,
                attempts=1,
            )
        except Exception as dlq_err:
            logger.warning(
                "Failed to capture Retell webhook DLQ event: %s",
                safe_error_summary(dlq_err),
            )

        if processing_call_id and processing_event_type:
            try:
                await _finish_webhook_processing(
                    processing_call_id,
                    processing_event_type,
                    status="FAILED",
                    error=safe_error,
                )
            except Exception:
                logger.warning("Failed to mark webhook event as FAILED")

        # Re-raise the original exception so Celery's autoretry takes
        # over with backoff. The handler that delayed this task already
        # returned 200 to the vendor; surfacing an HTTP error here would
        # be both useless (no client to receive it) and wrong (the task
        # framework wouldn't see a real failure).
        raise
