"""Retell webhook handlers for call events (call_analyzed, call_ended)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.app.config import settings
from src.app.gohighlevel.client import GHLClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retell", tags=["Retell Webhooks"])


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
    from src.app.services.tenant_service import TenantService
    from src.app.database import get_db_session
    
    tenant = None
    
    if agent_id:
        try:
            async for session in get_db_session():
                tenant_service = TenantService(session)
                tenant = await tenant_service.get_by_retell_agent_id(agent_id)
                break
        except Exception as e:
            logger.warning(f"Failed to lookup tenant by agent_id {agent_id}: {e}")
    
    # If tenant has GHL config, create tenant-specific client
    if tenant and tenant.ghl_api_key and tenant.ghl_location_id:
        client = GHLClient(
            api_key=tenant.ghl_api_key,
            location_id=tenant.ghl_location_id,
        )
        return client, tenant
    
    # Fall back to global client
    return get_ghl_client(), tenant


# ============================================================================
# Webhook Endpoint
# ============================================================================


@router.post("/webhook")
async def handle_retell_webhook(request: Request) -> dict[str, str]:
    """
    Handle Retell webhook events (call_analyzed, call_ended).

    This endpoint receives call data from Retell and forwards it to GoHighLevel
    for CRM integration. It creates/updates contacts with call details.

    Note: This endpoint does NOT require Retell signature verification
    because it's a custom webhook URL you configure in Retell dashboard,
    not the function calling endpoint.
    """
    try:
        body = await request.json()

        # Log full payload for debugging (exclude transcript to reduce log size)
        log_body = {k: v for k, v in body.get("call", {}).items() if k not in ["transcript", "transcript_object", "transcript_with_tool_calls"]}
        logger.info(f"Retell webhook payload: event={body.get('event')}, call_data={json.dumps(log_body, default=str)}")

        event = RetellWebhookEvent.model_validate(body)

        logger.info(f"Received Retell webhook: event={event.event}, call_id={event.call.call_id}")

        # Only process call_analyzed events (has full analysis data)
        if event.event != "call_analyzed":
            logger.info(f"Ignoring event type: {event.event}")
            return {"status": "ignored", "reason": f"Event type {event.event} not processed"}

        # Get tenant-aware GHL client (resolves from agent_id)
        ghl_client, tenant = await get_tenant_ghl_client(event.call.agent_id)
        if not ghl_client:
            logger.warning("GHL not configured (no global or tenant config), skipping contact sync")
            return {"status": "skipped", "reason": "GHL not configured"}
        
        tenant_info = f" for tenant '{tenant.slug}'" if tenant else " (global config)"
        logger.info(f"Using GHL client{tenant_info}")

        # Extract phone number (required)
        phone_number = event.call.from_number
        if not phone_number:
            logger.warning("No from_number in webhook, skipping")
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
            return {
                "status": "success",
                "ghl_contact_id": contact_id,
                "is_new_contact": str(result.get("new", False)),
            }

        except Exception as e:
            logger.error(f"Failed to sync to GHL: {e}")
            # Don't fail the webhook, just log the error
            return {"status": "error", "reason": str(e)}

    except Exception as e:
        logger.exception(f"Webhook processing error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
