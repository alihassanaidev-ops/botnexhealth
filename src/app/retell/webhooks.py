"""Retell webhook handlers for call events (call_analyzed, call_ended).

We accept only the PII-scrubbed variants from Retell — raw transcripts,
raw analysis, and the unscrubbed recording URL are intentionally ignored
at the webhook boundary. PHI never enters our datastore in raw form.
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


# ============================================================================
# Pydantic Models for Retell call_analyzed webhook
# ============================================================================


class CallAnalysisData(BaseModel):
    """Scrubbed analysis data extracted by Retell."""

    call_summary: str | None = Field(None, alias="call_summary")
    in_voicemail: bool | None = None
    user_sentiment: str | None = None
    call_successful: bool | None = None
    custom_analysis_data: dict[str, Any] = Field(default_factory=dict)


class RetellCallWebhook(BaseModel):
    """Call data from Retell webhook.

    Only the scrubbed variants of recording, transcript, and analysis are
    consumed. Raw fields received in the payload are ignored.
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
    if event.event != "call_analyzed":
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
    from src.app.tasks.webhooks import process_retell_call_analyzed

    process_retell_call_analyzed.delay(payload)

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

                # Only the scrubbed analysis is used. Raw analysis is never persisted.
                analysis_dict = (
                    event.call.scrubbed_call_analysis.model_dump()
                    if event.call.scrubbed_call_analysis
                    else {}
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
                    recording_url=event.call.scrubbed_recording_url,
                    transcript_with_tool_calls=event.call.scrubbed_transcript_with_tool_calls,
                )

                # Call service to save to DB (analysis_dict is always a dict now)
                saved_call = await post_call_service.process_call_analyzed_event(
                    institution_id=institution.id,
                    location_id=str(location.id) if location else None,
                    webhook_call=mapped_call_data,
                    analysis=analysis_dict,
                )

                # Commit the transaction so contacts and calls are saved!
                await session.commit()

            # ── Recording upload: enqueue S3 upload after DB commit ──
            _rec_url = event.call.scrubbed_recording_url
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

            # ── Auto-SMS: enqueue after commit (durable via Celery) ────────
            # The AI generates the SMS body as part of custom_analysis_data;
            # Retell preserves agent-generated outbound text through scrubbing
            # because it was synthesized for transmission, not extracted from
            # the patient's speech.
            _scrubbed_analysis = event.call.scrubbed_call_analysis
            _custom_analysis = (
                _scrubbed_analysis.custom_analysis_data if _scrubbed_analysis else {}
            )
            _sms_body: str | None = (_custom_analysis or {}).get("send_sms")
            _fallback_from = (
                _custom_analysis.get("from_number")
                or _custom_analysis.get("phone_number")
                or _custom_analysis.get("patient_phone")
            )
            if mapped_call_data.direction == "inbound":
                _patient_phone = mapped_call_data.from_number or _fallback_from
            elif mapped_call_data.direction == "outbound":
                _patient_phone = mapped_call_data.to_number or _fallback_from
            else:
                _patient_phone = (
                    mapped_call_data.from_number
                    or mapped_call_data.to_number
                    or _fallback_from
                )

            sms_body_present = bool(_sms_body)
            sms_body_len = len(_sms_body) if _sms_body else 0
            patient_phone_hash = (
                hash_for_logging(_patient_phone) if _patient_phone else None
            )
            location_id_hash = hash_for_logging(str(location.id)) if location else None
            missing_reasons: list[str] = []
            if not _sms_body:
                missing_reasons.append("missing_send_sms")
            if not _patient_phone:
                missing_reasons.append("missing_patient_phone")
            if not location:
                missing_reasons.append("missing_location")
            elif not location.twilio_from_number:
                missing_reasons.append("missing_twilio_from_number")

            if (
                _sms_body
                and _patient_phone
                and location
                and location.twilio_from_number
            ):
                try:
                    from src.app.tasks.sms import enqueue_auto_sms

                    enqueue_auto_sms(
                        from_number=location.twilio_from_number,  # type: ignore[arg-type]
                        to_number=_patient_phone,
                        body=_sms_body,
                        institution_location_id=location.id,
                        patient_contact_id=saved_call.contact_id,
                        call_id=saved_call.id,
                    )
                    logger.info(
                        "Auto-SMS enqueued: call_hash=%s to_hash=%s from_hash=%s location_hash=%s sms_len=%s",
                        hash_for_logging(event.call.call_id),
                        patient_phone_hash or "none",
                        hash_for_logging(location.twilio_from_number),
                        location_id_hash or "none",
                        sms_body_len,
                    )
                except Exception as sms_enqueue_err:
                    logger.error(
                        "Failed to enqueue auto-SMS: call_hash=%s error=%s",
                        hash_for_logging(event.call.call_id),
                        safe_error_summary(sms_enqueue_err),
                    )

            else:
                logger.info(
                    "Auto-SMS skipped: call_hash=%s reasons=%s sms_body=%s sms_len=%s patient_phone_hash=%s location_hash=%s twilio_from_configured=%s direction=%s",
                    hash_for_logging(event.call.call_id),
                    ",".join(missing_reasons) if missing_reasons else "unknown",
                    sms_body_present,
                    sms_body_len,
                    patient_phone_hash or "none",
                    location_id_hash or "none",
                    bool(location and location.twilio_from_number),
                    mapped_call_data.direction,
                )

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
