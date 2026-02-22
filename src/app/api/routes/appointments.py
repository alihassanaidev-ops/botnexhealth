"""Appointment routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.app.api.helpers import handle_nexhealth_request
from src.app.api.models import CreateAppointmentRequest, CancelAppointmentRequest
from src.app.api.deps import get_current_active_user
from src.app.config import Settings, get_settings
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.nexhealth.client import NexHealthClient
from src.app.services.audit_decorator import audit
from src.app.api.rate_limit import limiter, RATE_READ, RATE_WRITE

router = APIRouter(dependencies=[Depends(get_current_active_user)])


@router.get("/appointments")
@limiter.limit(RATE_READ)
@audit(
    AuditAction.READ_APPOINTMENT, 
    resource=lambda request, subdomain, location_id, start, end, **kwargs: 
        f"appts:{subdomain}:{location_id}:{start}_{end}",
    actor=AuditActor.API_CLIENT
)
async def list_appointments(
    request: Request,
    start: str,
    end: str,
    subdomain: str | None = Query(None, description="Institution subdomain (required if not in settings)"),
    location_id: int | None = Query(None, description="Location ID (required if not in settings)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    include_procedures: bool = False,
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    List appointments from NexHealth API.

    Args:
        start: Start date (YYYY-MM-DD)
        end: End date (YYYY-MM-DD)
        subdomain: Institution subdomain (uses settings if not provided)
        location_id: Location ID (uses settings if not provided)
        page: Page number
        per_page: Items per page
        include_procedures: Include procedure data
        settings: Injected settings
        client: Injected NexHealth client

    Returns:
        NexHealth API response
    """
    # Use provided params or fall back to settings
    subdomain = subdomain or settings.nexhealth_subdomain
    location_id = location_id or settings.nexhealth_location_id

    if not subdomain or not location_id:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain or location_id. Provide as query params or configure in settings.",
        )

    params: dict[str, Any] = {
        "subdomain": subdomain,
        "location_id": location_id,
        "start": start,
        "end": end,
        "page": page,
        "per_page": per_page,
    }
    if include_procedures:
        params["include[]"] = "procedures"

    return await handle_nexhealth_request(client, "GET", "/appointments", params=params)


@router.post("/appointments")
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.BOOK_APPOINTMENT, 
    resource=lambda request, body, **kwargs: f"new_appt_for:{body.appt.patient_id}",
    actor=AuditActor.API_CLIENT
)
async def book_appointment(
    request: Request,
    body: CreateAppointmentRequest,
    subdomain: str | None = Query(None, description="Institution subdomain (required if not in settings)"),
    location_id: int | None = Query(None, description="Location ID (required if not in settings)"),
    notify_patient: bool = Query(True, description="Notify patient via email/SMS"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """Book an appointment."""
    subdomain = subdomain or settings.nexhealth_subdomain
    location_id = location_id or settings.nexhealth_location_id

    if not subdomain or not location_id:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain or location_id. Provide as query params or configure in settings.",
        )

    params: dict[str, Any] = {
        "subdomain": subdomain,
        "location_id": location_id,
        "notify_patient": notify_patient
    }

    return await handle_nexhealth_request(client, "POST", "/appointments", params=params, json=body.model_dump())


@router.patch("/appointments/{id}")
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CANCEL_APPOINTMENT, 
    resource=lambda request, id, **kwargs: f"appointment:{id}",
    actor=AuditActor.API_CLIENT
)
async def cancel_appointment(
    request: Request,
    id: int,
    body: CancelAppointmentRequest,
    subdomain: str | None = Query(None, description="Institution subdomain (required if not in settings)"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """Cancel (or update) an appointment."""
    subdomain = subdomain or settings.nexhealth_subdomain

    if not subdomain:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain. Provide as query param or configure in settings.",
        )

    params: dict[str, Any] = {
        "subdomain": subdomain
    }

    return await handle_nexhealth_request(client, "PATCH", f"/appointments/{id}", params=params, json=body.model_dump())
