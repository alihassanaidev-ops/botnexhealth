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
from src.app.gohighlevel.client import GHLClient
from src.app.retell.security import get_retell_secret, get_signature_dependency, hash_for_logging
from src.app.services.call_events import call_event_broker

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
    transcript: str | None = None
    recording_url: str | None = None
    disconnection_reason: str | None = None
    call_analysis: CallAnalysisData | None = None


class RetellWebhookEvent(BaseModel):
    """Retell webhook event envelope."""
    event: str
    call: RetellCallWebhook


# ============================================================================
# GHL Client - Tenant Aware
# ============================================================================

# Global client fallback
_ghl_client: GHLClient | None = None


def get_ghl_client() -> GHLClient | None:
    """Get global GHL client singleton (fallback when no tenant)."""
    global _ghl_client
    if _ghl_client is None and settings.ghl_api_key:
        _ghl_client = GHLClient(
            api_key=settings.ghl_api_key,
            location_id=settings.ghl_location_id,
        )
    return _ghl_client


async def get_tenant_ghl_client(agent_id: str | None) -> tuple[GHLClient | None, "Tenant | None"]:
    """
    Get GHL client for the tenant associated with the given agent_id.
    
    Returns (client, tenant) tuple. Falls back to global client if no tenant found.
    """
    from src.app.database import get_db_session
    from src.app.services.tenant_service import TenantService
    
    tenant = None
    location = None
    
    if agent_id:
        try:
            async with get_db_session() as session:
                tenant_service = TenantService(session)
                result = await tenant_service.get_location_by_retell_agent_id(agent_id)
                if result:
                    location, tenant = result
        except Exception as e:
            logger.warning(f"Failed to lookup tenant by agent_id {agent_id}: {e}")
    
    # If tenant has GHL config, create tenant-specific client
    if tenant and location and tenant.ghl_api_key and location.ghl_location_id:
        client = GHLClient(
            api_key=tenant.ghl_api_key,
            location_id=location.ghl_location_id,
        )
        return client, tenant
    
    # Fall back to global client
    return get_ghl_client(), tenant


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
    ghl_contact_id: str | None = None,
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
        row.ghl_contact_id = ghl_contact_id or row.ghl_contact_id
        row.last_error = error
        row.updated_at = datetime.now(timezone.utc)


async def _publish_call_data_event(
    tenant_id: str,
    event_type: str,
    call_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Emit tenant-scoped call data freshness signal for dashboard clients."""
    await call_event_broker.publish(
        tenant_id=tenant_id,
        event_type=event_type,
        payload={
            "call_id": call_id,
            **(details or {}),
        },
    )


# ============================================================================
# Webhook Endpoint
# ============================================================================


@router.post("/webhook")
async def handle_retell_webhook(
    body: bytes = Depends(verify_webhook_signature),
) -> dict[str, str]:
    """
    Handle Retell webhook events (call_analyzed, call_ended).

    This endpoint receives call data from Retell and forwards it to GoHighLevel
    for CRM integration. It creates/updates contacts with call details.

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

        # Get tenant-aware GHL client (resolves from agent_id)
        ghl_client, tenant = await get_tenant_ghl_client(event.call.agent_id)
        if not ghl_client:
            logger.warning("GHL not configured (no global or tenant config), skipping contact sync")
            await _finish_webhook_processing(
                processing_call_id,
                processing_event_type,
                status="COMPLETED",
                tenant_id=tenant.id if tenant else None,
                error="GHL not configured",
            )
            return {"status": "skipped", "reason": "GHL not configured"}
        
        tenant_info = f" for tenant '{tenant.slug}'" if tenant else " (global config)"
        logger.info(f"Using GHL client{tenant_info}")

        # Extract phone number (required)
        phone_number = event.call.from_number
        if not phone_number:
            logger.warning("No from_number in webhook, skipping")
            await _finish_webhook_processing(
                processing_call_id,
                processing_event_type,
                status="COMPLETED",
                tenant_id=tenant.id if tenant else None,
                error="No phone number in webhook",
            )
            return {"status": "skipped", "reason": "No phone number"}

        # Extract call analysis data
        call_summary: str | None = None
        appointment_details: str | None = None
        patient_name: str | None = None
        patient_dob: str | None = None
        patient_email: str | None = None

        if event.call.call_analysis:
            call_summary = event.call.call_analysis.call_summary
            custom_data = event.call.call_analysis.custom_analysis_data

            # Extract custom analysis fields (configured in Retell)
            appointment_details = custom_data.get("Appointment Detail")
            patient_name = custom_data.get("Patient name")
            patient_dob = custom_data.get("Date of birth")
            patient_email = custom_data.get("Patient email")

            # Send to GHL
        try:
            result = await ghl_client.upsert_contact_from_retell(
                phone_number=phone_number,
                call_summary=call_summary,
                appointment_details=appointment_details,
                recording_url=event.call.recording_url,
                duration_ms=event.call.duration_ms,
                transcript=event.call.transcript,
                patient_name=patient_name if patient_name else None,
                patient_dob=patient_dob if patient_dob else None,
                patient_email=patient_email if patient_email else None,
            )

            contact_id = result.get("contact", {}).get("id", "unknown")
            
            # Audit GHL Sync
            from src.app.services.audit import log_audit_background, AuditAction, AuditActor, AuditOutcome
            log_audit_background(
                actor=AuditActor.RETELL_AGENT,  # or SYSTEM/GHL, but triggered by Retell webhook
                action=AuditAction.SYNC_GHL_CONTACT,
                target_resource=f"contact:{contact_id}",
                outcome=AuditOutcome.SUCCESS,
                metadata={
                    "event_type": event.event,
                    "call_id": hash_for_logging(event.call.call_id),
                    "is_new": result.get("new", False)
                },
                tenant_id=tenant.id if tenant else None,
            )

            await _finish_webhook_processing(
                processing_call_id,
                processing_event_type,
                status="COMPLETED",
                tenant_id=tenant.id if tenant else None,
                ghl_contact_id=contact_id,
            )

            if tenant:
                await _publish_call_data_event(
                    tenant_id=tenant.id,
                    event_type="data_changed",
                    call_id=event.call.call_id,
                    details={
                        "source": "retell_webhook",
                        "retell_event": event.event,
                    },
                )
            
            return {
                "status": "success",
                "ghl_contact_id": contact_id,
                "is_new_contact": str(result.get("new", False)),
            }

        except Exception as e:
            logger.error(f"Failed to sync to GHL: {e}")
            
            # Audit GHL Sync Failure
            from src.app.services.audit import log_audit_background, AuditAction, AuditActor, AuditOutcome
            log_audit_background(
                actor=AuditActor.RETELL_AGENT,
                action=AuditAction.SYNC_GHL_CONTACT,
                target_resource=f"phone:{hash_for_logging(phone_number) if phone_number else 'unknown'}",
                outcome=AuditOutcome.FAILURE_EXTERNAL_API,
                metadata={
                    "event_type": event.event,
                    "call_id": hash_for_logging(event.call.call_id),
                    "error": str(e)
                },
                tenant_id=tenant.id if tenant else None,
            )

            await _finish_webhook_processing(
                processing_call_id,
                processing_event_type,
                status="FAILED",
                tenant_id=tenant.id if tenant else None,
                error=str(e),
            )
            if tenant:
                await _publish_call_data_event(
                    tenant_id=tenant.id,
                    event_type="data_sync_error",
                    call_id=event.call.call_id,
                    details={
                        "source": "retell_webhook",
                        "retell_event": event.event,
                    },
                )
            
            # Don't fail the webhook, just log the error
            return {"status": "error", "reason": str(e)}

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
