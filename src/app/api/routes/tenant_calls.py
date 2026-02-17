"""
Tenant calls routes — proxy GHL Opportunities API for client dashboard.

Fetches call data from GoHighLevel using the tenant's encrypted API key.
No GHL credentials are ever exposed to the frontend.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api.deps import get_current_active_user
from src.app.database import get_db_session_dep
from src.app.gohighlevel.client import GHLClient
from src.app.models.tenant import Tenant
from src.app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant/calls", tags=["Tenant Calls"])


# ── Response Models ──────────────────────────────────────────────────────


class CallRecord(BaseModel):
    """Processed call record returned to the frontend."""
    id: str
    patient_name: str
    contact_id: str | None = None
    phone: str | None = None
    email: str | None = None
    call_date: str | None = None
    call_time: str | None = None
    call_duration: str | None = None
    call_status: str | None = None
    call_status_normalized: str | None = None
    call_summary: str | None = None
    call_transcript: str | None = None
    recording_link: str | None = None
    next_action: str | None = None
    patient_intent: str | None = None
    new_patient: str | None = None
    preferred_callback_time: str | None = None
    times_called: str | None = None
    agent_used: str | None = None
    complaining_patient: str | None = None
    insurance_and_billing: str | None = None
    patient_contact_status: str | None = None
    monetary_value: float | None = None
    pipeline_stage: str | None = None


class CallsResponse(BaseModel):
    """Response envelope for calls endpoint."""
    calls: list[CallRecord]
    total: int
    counts: dict[str, int]


# ── Helpers ──────────────────────────────────────────────────────────────


def _get_custom_field_value(
    opportunity: dict[str, Any],
    field_id: str | None,
) -> str | None:
    """Extract a custom field value from a GHL opportunity."""
    if not field_id or not opportunity.get("customFields"):
        return None

    for field in opportunity["customFields"]:
        if field.get("id") == field_id:
            raw_date = field.get("fieldValueDate")
            date_val: str | None = None
            if raw_date:
                if isinstance(raw_date, (int, float)):
                    # GHL returns epoch milliseconds for date fields
                    date_val = datetime.fromtimestamp(raw_date / 1000).strftime("%Y-%m-%d")
                elif isinstance(raw_date, str):
                    date_val = datetime.fromisoformat(raw_date).strftime("%Y-%m-%d")
                else:
                    date_val = str(raw_date)
            str_val = field.get("fieldValueString")
            if str_val:
                return str_val
            num_val = field.get("fieldValueNumber")
            if num_val is not None:
                return str(num_val)
            generic = field.get("value")
            if generic:
                return str(generic)
            return date_val
    return None


def _normalize_status(raw_status: str | None) -> str:
    """Normalize a call status string for filtering and grouping."""
    if not raw_status:
        return "unknown"
    cleaned = raw_status.strip().lower()
    # Remove emojis and extra punctuation
    cleaned = "".join(c for c in cleaned if c.isalnum() or c.isspace())
    cleaned = " ".join(cleaned.split())
    return cleaned


def _status_bucket(normalized: str) -> str:
    """Map a normalized status to one of the 6 dashboard buckets."""
    if "booking" in normalized or "book" in normalized:
        return "need_booking"
    if "cancel" in normalized:
        return "need_cancellation"
    if "reschedule" in normalized:
        return "need_reschedule"
    if "emergency" in normalized:
        return "need_emergency"
    if "follow" in normalized:
        return "needs_follow_up"
    if "no action" in normalized or "noaction" in normalized:
        return "no_action"
    return "no_action"


def _process_opportunity(
    opp: dict[str, Any],
    field_map: dict[str, str],
) -> CallRecord | None:
    """Transform a raw GHL opportunity into a CallRecord."""
    agent_used = _get_custom_field_value(opp, field_map.get("agent_used"))

    # Filter: only include calls from our Retell agent
    # Accept if agent_used is not mapped or if it matches
    if field_map.get("agent_used") and (
        not agent_used
    ):
        return None

    call_date = _get_custom_field_value(opp, field_map.get("call_date"))
    if not call_date:
        return None

    raw_status = _get_custom_field_value(opp, field_map.get("call_status")) or ""
    normalized = _normalize_status(raw_status)

    return CallRecord(
        id=opp.get("id", ""),
        patient_name=(
            opp.get("name")
            or opp.get("contact", {}).get("name")
            or "Unknown Patient"
        ),
        contact_id=opp.get("contact", {}).get("id"),
        phone=_get_custom_field_value(opp, field_map.get("phone")) or opp.get("contact", {}).get("phone"),
        email=opp.get("contact", {}).get("email"),
        call_date=call_date,
        call_time=_get_custom_field_value(opp, field_map.get("call_time")),
        call_duration=_get_custom_field_value(opp, field_map.get("call_duration")) or "0:00",
        call_status=raw_status,
        call_status_normalized=_status_bucket(normalized),
        call_summary=_get_custom_field_value(opp, field_map.get("call_summary")),
        call_transcript=_get_custom_field_value(opp, field_map.get("call_transcript")),
        recording_link=_get_custom_field_value(opp, field_map.get("recording_link")),
        next_action=_get_custom_field_value(opp, field_map.get("next_action")),
        patient_intent=_get_custom_field_value(opp, field_map.get("patient_intent")),
        new_patient=_get_custom_field_value(opp, field_map.get("new_patient")),
        preferred_callback_time=_get_custom_field_value(opp, field_map.get("preferred_callback_time")),
        times_called=_get_custom_field_value(opp, field_map.get("times_called")),
        agent_used=agent_used,
        complaining_patient=_get_custom_field_value(opp, field_map.get("complaining_patient")),
        insurance_and_billing=_get_custom_field_value(opp, field_map.get("insurance_and_billing")),
        patient_contact_status=_get_custom_field_value(opp, field_map.get("patient_contact_status")),
        monetary_value=opp.get("monetaryValue"),
        pipeline_stage=opp.get("pipelineStage", {}).get("name"),
    )


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("", response_model=CallsResponse)
async def list_calls(
    user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_db_session_dep)],
    status_filter: str | None = Query(None, alias="status", description="Filter by call status bucket"),
    search: str | None = Query(None, description="Search patient name"),
    page: int = Query(1, ge=1, description="Page number"),
) -> CallsResponse:
    """
    List patient calls from GoHighLevel.

    Uses the tenant's encrypted GHL API key to proxy the GHL Opportunities API.
    """
    if not user.tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User is not associated with a tenant")

    tenant = (
        await session.execute(
            select(Tenant).where(Tenant.id == user.tenant_id, Tenant.is_active == True)
        )
    ).scalar_one_or_none()

    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    # Verify GHL credentials exist
    ghl_key = tenant.ghl_api_key
    ghl_location = tenant.ghl_location_id

    if not ghl_key or not ghl_location:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "GoHighLevel credentials are not configured for this tenant",
        )

    # Get custom field mapping (or empty dict)
    field_map: dict[str, str] = tenant.ghl_custom_fields or {}

    # Fetch from GHL
    client = GHLClient(api_key=ghl_key, location_id=ghl_location)
    try:
        result = await client.search_opportunities(limit=100, page=page)
    except Exception as e:
        logger.error(f"Failed to fetch calls from GHL: {e}")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Failed to fetch data from GoHighLevel",
        )
    finally:
        await client.close()

    # Process opportunities
    raw_opportunities = result.get("opportunities", [])
    calls: list[CallRecord] = []

    for opp in raw_opportunities:
        record = _process_opportunity(opp, field_map)
        if record:
            calls.append(record)

    # Sort by call date (newest first)
    calls.sort(key=lambda c: c.call_date or "", reverse=True)

    # Apply filters
    if status_filter:
        calls = [c for c in calls if c.call_status_normalized == status_filter]

    if search:
        search_lower = search.lower()
        calls = [c for c in calls if search_lower in c.patient_name.lower()]

    # Count by status bucket
    counts: dict[str, int] = {
        "need_booking": 0,
        "need_cancellation": 0,
        "need_reschedule": 0,
        "need_emergency": 0,
        "needs_follow_up": 0,
        "no_action": 0,
    }
    # Use unfiltered calls for counts but recalculate from all processed
    for opp in raw_opportunities:
        record = _process_opportunity(opp, field_map)
        if record and record.call_status_normalized:
            bucket = record.call_status_normalized
            if bucket in counts:
                counts[bucket] += 1

    return CallsResponse(
        calls=calls,
        total=len(calls),
        counts=counts,
    )
