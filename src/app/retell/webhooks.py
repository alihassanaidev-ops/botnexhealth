"""Retell webhook handlers for call events (call_analyzed, call_ended)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.app.config import settings
from src.app.retell.security import get_retell_secret, get_signature_dependency, hash_for_logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retell", tags=["Retell Webhooks"])

# Create signature verification dependency
verify_webhook_signature = get_signature_dependency(get_retell_secret)


# ============================================================================
# Pydantic Models for Retell call_analyzed webhook
# ============================================================================


class CallAnalysisData(BaseModel):
    """Custom analysis data extracted by Retell."""
    call_summary: str | None = Field(None, alias="call_summary")
    in_voicemail: bool | None = None
    user_sentiment: str | None = None
    call_successful: bool | None = None
    custom_analysis_data: dict[str, Any] = Field(default_factory=dict)


class RetellCallWebhook(BaseModel):
    """Call data from Retell webhook."""
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
    transcript: str | None = None
    recording_url: str | None = None
    scrubbed_recording_url: str | None = None   # PII-scrubbed version (preferred for HIPAA)
    # Structured turn-by-turn transcript arrays from Retell
    transcript_with_tool_calls: list[dict] | None = None        # full unredacted
    scrubbed_transcript_with_tool_calls: list[dict] | None = None  # PII-scrubbed (HIPAA)
    disconnection_reason: str | None = None
    call_analysis: CallAnalysisData | None = None
    scrubbed_call_analysis: CallAnalysisData | None = None  # PII-scrubbed (preferred for HIPAA)
    # Dynamic variables collected during the call (name, email, etc.)
    collected_dynamic_variables: dict[str, Any] = Field(default_factory=dict)


class RetellWebhookEvent(BaseModel):
    """Retell webhook event envelope."""
    event: str
    call: RetellCallWebhook


async def _begin_webhook_processing(call_id: str, event_type: str) -> tuple[bool, str]:
    """Create or claim idempotency record for a webhook event.

    Returns:
        (can_process, reason)
    """
    from src.app.database import get_db_session
    from src.app.models.retell_webhook_event import RetellWebhookEvent, RetellWebhookStatus

    async with get_db_session() as session:
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
    """Update idempotency row after webhook processing finishes."""
    from src.app.database import get_db_session
    from src.app.models.retell_webhook_event import RetellWebhookEvent

    async with get_db_session() as session:
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


# ============================================================================
# Webhook Endpoint
# ============================================================================


@router.post("/webhook")
async def handle_retell_webhook(
    body: bytes = Depends(verify_webhook_signature),
) -> dict[str, str]:
    """
    Handle Retell webhook events (call_analyzed, call_ended).

    This endpoint receives call data from Retell and records webhook events
    for idempotency tracking.

    Security: Requires valid Retell signature (x-retell-signature header).
    """
    processing_started = False
    processing_call_id: str | None = None
    processing_event_type: str | None = None

    try:
        # Parse the verified body (bytes -> dict)
        payload = json.loads(body)

        event = RetellWebhookEvent.model_validate(payload)

        logger.info(f"Received Retell webhook: event={event.event}, call_id={hash_for_logging(event.call.call_id)}")

        # Only process call_analyzed events (has full analysis data)
        if event.event != "call_analyzed":
            logger.info(f"Ignoring event type: {event.event}")
            return {"status": "ignored", "reason": f"Event type {event.event} not processed"}

        # Idempotency guard for retried webhooks
        processing_call_id = event.call.call_id
        processing_event_type = event.event
        can_process, reason = await _begin_webhook_processing(processing_call_id, processing_event_type)
        if not can_process:
            logger.info(
                f"Skipping duplicate Retell webhook: call_id={processing_call_id}, "
                f"event={processing_event_type}, reason={reason}"
            )
            return {"status": "duplicate", "reason": reason}
        processing_started = True

        # Resolve institution + location from agent_id
        institution = None
        location = None
        if event.call.agent_id:
            try:
                from src.app.database import get_db_session
                from src.app.services.institution_service import InstitutionService

                async with get_db_session() as session:
                    institution_service = InstitutionService(session)
                    result = await institution_service.get_location_by_retell_agent_id(event.call.agent_id)
                    if result:
                        location, institution = result
            except Exception as e:
                logger.warning(f"Failed to lookup institution by agent_id {event.call.agent_id}: {e}")

        # Audit webhook received
        from src.app.services.audit import log_audit_background, AuditAction, AuditActor, AuditOutcome
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
        )

        # Process Contact & Call records if we identified the institution
        if institution:
            from src.app.database import get_db_session
            from src.app.services.post_call_service import PostCallService
            
            async with get_db_session() as session:
                post_call_service = PostCallService(session)
                
                # event.call is RetellCallWebhook, map it to the expected dict for analysis
                # Prefer PII-scrubbed analysis for HIPAA; fall back to raw only if scrubbed absent
                _analysis_src = event.call.scrubbed_call_analysis or event.call.call_analysis
                analysis_dict = _analysis_src.model_dump() if _analysis_src else {}
                # Merge top-level collected_dynamic_variables so the service can use them
                # as a fallback for name/email when custom_analysis_data fields are missing
                analysis_dict["collected_dynamic_variables"] = event.call.collected_dynamic_variables or {}
                
                # Transform to RetellCallData format expected by service
                # The webhook event structure differs slightly from the direct API structure
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
                    transcript=event.call.transcript,
                    # Prefer PII-scrubbed URL for HIPAA; fall back to raw only if scrubbed absent
                    recording_url=event.call.scrubbed_recording_url or event.call.recording_url,
                    # Structured JSONB transcripts (turn-by-turn with tool calls)
                    transcript_with_tool_calls=event.call.transcript_with_tool_calls,
                    scrubbed_transcript_with_tool_calls=event.call.scrubbed_transcript_with_tool_calls,
                )
                
                # Call service to save to DB (analysis_dict is always a dict now)
                saved_call = await post_call_service.process_call_analyzed_event(
                    institution_id=institution.id,
                    webhook_call=mapped_call_data,
                    analysis=analysis_dict,
                )
                
                # Commit the transaction so contacts and calls are saved!
                await session.commit()

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
                        "custom_analysis_data": analysis_dict.get("custom_analysis_data") or {},
                        "collected_dynamic_variables": analysis_dict.get("collected_dynamic_variables") or {},
                    },
                )
            except Exception as email_enqueue_err:
                logger.error(
                    "Failed to enqueue call email notification: call=%s error=%s",
                    hash_for_logging(event.call.call_id),
                    email_enqueue_err,
                )

            # ── In-app notification: enqueue after DB commit (durable via Celery) ──
            try:
                from src.app.tasks.in_app_notifications import enqueue_in_app_notifications

                enqueue_in_app_notifications(
                    call_id=saved_call.id,
                    institution_id=institution.id,
                    location_id=location.id if location else None,
                    call_status=saved_call.call_status,
                    call_tags_csv=saved_call.call_tags,
                )
            except Exception as in_app_enqueue_err:
                logger.error(
                    "Failed to enqueue in-app notification: call=%s error=%s",
                    hash_for_logging(event.call.call_id),
                    in_app_enqueue_err,
                )

            # ── Auto-SMS: enqueue after commit (durable via Celery) ────────
            # Use raw call_analysis (not scrubbed) so patient name is intact.
            _raw_analysis = event.call.call_analysis
            _sms_body: str | None = (
                (_raw_analysis.custom_analysis_data or {}).get("send_sms")
                if _raw_analysis
                else None
            )
            _patient_phone = (
                mapped_call_data.from_number
                if mapped_call_data.direction == "inbound"
                else mapped_call_data.to_number
            ) or mapped_call_data.from_number

            if _sms_body and _patient_phone and location and location.twilio_from_number:
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
                except Exception as sms_enqueue_err:
                    logger.error(
                        "Failed to enqueue auto-SMS: call=%s error=%s",
                        hash_for_logging(event.call.call_id),
                        sms_enqueue_err,
                    )

            elif location and location.twilio_from_number and not _sms_body:
                logger.debug(
                    "Auto-SMS skipped: no send_sms content in call analysis for call=%s",
                    hash_for_logging(event.call.call_id),
                )

        await _finish_webhook_processing(
            processing_call_id,
            processing_event_type,
            status="COMPLETED",
            institution_id=institution.id if institution else None,
        )

        return {
            "status": "success",
            "event": event.event,
            "call_id": hash_for_logging(event.call.call_id),
        }

    except Exception as e:
        logger.exception(f"Webhook processing error: {e}")
        
        # Audit Webhook Failure (General)
        try:
            from src.app.services.audit import log_audit_background, AuditAction, AuditActor, AuditOutcome
            log_audit_background(
                actor=AuditActor.RETELL_AGENT,
                action=AuditAction.WEBHOOK_RECEIVED,
                target_resource="webhook:retell",
                outcome=AuditOutcome.FAILURE_INTERNAL,
                metadata={"error": str(e)},
            )
        except Exception:
            pass # Failsafe

        if processing_started and processing_call_id and processing_event_type:
            try:
                await _finish_webhook_processing(
                    processing_call_id,
                    processing_event_type,
                    status="FAILED",
                    error=str(e),
                )
            except Exception:
                logger.warning("Failed to mark webhook event as FAILED")
            
        raise HTTPException(status_code=400, detail=str(e))
