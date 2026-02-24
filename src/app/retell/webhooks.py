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
    disconnection_reason: str | None = None
    call_analysis: CallAnalysisData | None = None
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
    tenant_id: str | None = None,
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
        row.tenant_id = tenant_id or row.tenant_id
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

        # Resolve tenant from agent_id
        tenant = None
        if event.call.agent_id:
            try:
                from src.app.database import get_db_session
                from src.app.services.tenant_service import TenantService

                async with get_db_session() as session:
                    tenant_service = TenantService(session)
                    result = await tenant_service.get_location_by_retell_agent_id(event.call.agent_id)
                    if result:
                        _, tenant = result
            except Exception as e:
                logger.warning(f"Failed to lookup tenant by agent_id {event.call.agent_id}: {e}")

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
            tenant_id=tenant.id if tenant else None,
        )

        # Process Contact & Call records if we identified the tenant clinic
        if tenant:
            from src.app.database import get_db_session
            from src.app.services.post_call_service import PostCallService
            
            async with get_db_session() as session:
                post_call_service = PostCallService(session)
                
                # event.call is RetellCallWebhook, map it to the expected dict for analysis
                analysis_dict = event.call.call_analysis.model_dump() if event.call.call_analysis else {}
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
                )
                
                # Call service to save to DB (analysis_dict is always a dict now)
                await post_call_service.process_call_analyzed_event(
                    tenant_id=tenant.id,
                    webhook_call=mapped_call_data,
                    analysis=analysis_dict,
                )
                
                # Commit the transaction so contacts and calls are saved!
                await session.commit()
                
        await _finish_webhook_processing(
            processing_call_id,
            processing_event_type,
            status="COMPLETED",
            tenant_id=tenant.id if tenant else None,
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
