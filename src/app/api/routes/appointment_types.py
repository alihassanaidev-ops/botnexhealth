"""Appointment Types routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from src.app.api.helpers import handle_nexhealth_request
from src.app.api.models import AppointmentTypeDetailResponse, AppointmentTypeListResponse
from src.app.api.routes.base import verify_admin_key
from src.app.config import Settings
from src.app.dependencies import get_nexhealth_client_dependency, get_settings
from src.app.nexhealth.client import NexHealthClient

router = APIRouter(dependencies=[Depends(verify_admin_key)])


@router.get("/appointment_types", response_model=AppointmentTypeListResponse)
async def list_appointment_types(
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    location_id: int | None = Query(None, description="Id of the associated location, required when appointment types are location specific"),
    include: list[str] | None = Query(None, alias="include[]", description="Resources to include (descriptors)"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    View appointment types.

    Appointment types help define what kinds of appointments providers serve.
    They're useful when manually configuring provider schedules.
    """
    subdomain = subdomain or settings.nexhealth_subdomain

    if not subdomain:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain. Provide as query param or configure in settings.",
        )

    params: dict[str, Any] = {"subdomain": subdomain}

    if location_id:
        params["location_id"] = location_id
    if include:
        params["include[]"] = include

    return await handle_nexhealth_request(client, "GET", "/appointment_types", params=params)


@router.get("/appointment_types/{id}", response_model=AppointmentTypeDetailResponse)
async def get_appointment_type(
    id: int = Path(..., description="The appointment type ID"),
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    location_id: int | None = Query(None, description="Id of the associated location, required when appointment types are location specific"),
    include: list[str] | None = Query(None, alias="include[]", description="Resources to include (descriptors)"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """View an appointment type."""
    subdomain = subdomain or settings.nexhealth_subdomain

    if not subdomain:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain. Provide as query param or configure in settings.",
        )

    params: dict[str, Any] = {"subdomain": subdomain}

    if location_id:
        params["location_id"] = location_id
    if include:
        params["include[]"] = include

    return await handle_nexhealth_request(client, "GET", f"/appointment_types/{id}", params=params)
