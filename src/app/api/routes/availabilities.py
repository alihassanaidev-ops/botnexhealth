"""Availability routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from src.app.api.helpers import handle_nexhealth_request
from src.app.api.models import AvailabilityDetailResponse, AvailabilityListResponse
from src.app.api.deps import get_current_active_user
from src.app.config import Settings, get_settings
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.nexhealth.client import NexHealthClient

router = APIRouter(dependencies=[Depends(get_current_active_user)])


@router.get("/availabilities", response_model=AvailabilityListResponse)
async def list_availabilities(
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    location_id: int | None = Query(None, description="Used to scope the request to the specified location"),
    page: int = Query(1, ge=1, description="Page number, starts with page 1"),
    per_page: int = Query(5, ge=1, le=300, description="Number of results per page"),
    provider_id: int | None = Query(None, description="Filter for specific provider"),
    operatory_id: int | None = Query(None, description="Filter for specific operatory"),
    active: bool | None = Query(True, description="Filter active/not active"),
    ignore_past_dates: bool | None = Query(False, description="Filter out availabilities configured for specific dates in the past"),
    include: list[str] | None = Query(None, alias="include[]", description="Resources to include (e.g., appointment_types)"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    View availabilities.
    
    Returns provider availabilities with their configured time blocks,
    days, and associated appointment types.
    
    Note: The `synced` attribute indicates whether the availability was synced
    from the practice's health record system or manually created.
    """
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
        "page": page,
        "per_page": per_page,
    }
    if provider_id:
        params["provider_id"] = provider_id
    if operatory_id:
        params["operatory_id"] = operatory_id
    if active is not None:
        params["active"] = active
    if ignore_past_dates is not None:
        params["ignore_past_dates"] = ignore_past_dates
    if include:
        params["include[]"] = include

    return await handle_nexhealth_request(client, "GET", "/availabilities", params=params)


@router.get("/availabilities/{availability_id}", response_model=AvailabilityDetailResponse)
async def get_availability(
    availability_id: int = Path(..., description="Id of the availability"),
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    include: list[str] | None = Query(None, alias="include[]", description="Resources to include (e.g., appointment_types)"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    View a single availability.
    
    Note: The `synced` attribute indicates whether the availability was synced
    from the practice's health record system or manually created.
    """
    subdomain = subdomain or settings.nexhealth_subdomain

    if not subdomain:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain. Provide as query param or configure in settings.",
        )

    params: dict[str, Any] = {"subdomain": subdomain}
    if include:
        params["include[]"] = include

    return await handle_nexhealth_request(client, "GET", f"/availabilities/{availability_id}", params=params)
