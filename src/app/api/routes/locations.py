"""Location routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from src.app.api.helpers import handle_nexhealth_request
from src.app.api.models import (
    AppointmentDescriptorListResponse,
    InstitutionBasicListResponse,
    LocationDetailResponse,
)
from src.app.api.deps import get_current_user
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.nexhealth.client import NexHealthClient

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/locations", response_model=InstitutionBasicListResponse)
async def list_locations(
    subdomain: str | None = Query(None, description="Scope request to the specified institution"),
    inactive: bool | None = Query(None, description="Filter by inactive status"),
    foreign_id: str | None = Query(None, description="Find location by the integrated system Id"),
    filter_by_subscription_feature: str | None = Query(
        None,
        description="Filter by subscription feature (messaging, campaigns, reviews, etc.)",
    ),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """List locations (grouped by institution)."""
    params: dict[str, Any] = {
        "page": page,
        "per_page": per_page,
    }
    if subdomain:
        params["subdomain"] = subdomain
    if inactive is not None:
        params["inactive"] = inactive
    if foreign_id:
        params["foreign_id"] = foreign_id
    if filter_by_subscription_feature:
        params["filter_by_subscription_feature"] = filter_by_subscription_feature

    return await handle_nexhealth_request(client, "GET", "/locations", params=params)


@router.get("/locations/{location_id}", response_model=LocationDetailResponse)
async def get_location(
    location_id: int = Path(..., description="Location ID"),
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """Get a specific location by ID."""
    return await handle_nexhealth_request(client, "GET", f"/locations/{location_id}")


@router.get("/locations/{location_id}/appointment_descriptors", response_model=AppointmentDescriptorListResponse)
async def list_appointment_descriptors(
    location_id: int = Path(..., description="Location ID"),
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """List appointment descriptors for a location."""
    return await handle_nexhealth_request(
        client, "GET", f"/locations/{location_id}/appointment_descriptors"
    )
