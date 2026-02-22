"""Provider routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from src.app.api.helpers import handle_nexhealth_request
from src.app.api.models import ProviderDetailResponse, ProviderListResponse
from src.app.api.deps import get_current_active_user
from src.app.config import Settings, get_settings
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.nexhealth.client import NexHealthClient
from src.app.api.rate_limit import limiter, RATE_READ

router = APIRouter(dependencies=[Depends(get_current_active_user)])


@router.get("/providers", response_model=ProviderListResponse)
@limiter.limit(RATE_READ)
async def list_providers(
    request: Request,
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    location_id: int | None = Query(None, description="Only return providers associated with the specified Location Id"),
    ids: list[int] | None = Query(None, alias="ids[]", description="NexHealth IDs"),
    foreign_id: str | None = Query(None, description="Unique provider id from the EMR"),
    requestable: bool | None = Query(None, description="Only return providers who are bookable via NexHealth online booking"),
    inactive: bool | None = Query(None, description="Filter result set based on inactive status"),
    updated_since: str | None = Query(None, description="Query providers updated since datetime (ISO8601)"),
    include: list[str] | None = Query(None, alias="include[]", description="Resources to include (locations, availabilities, appointment_types)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(5, ge=1, le=300),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    View providers.

    Providers represent practice employees who can be booked for an appointment.
    To receive requestable providers only, specify the location_id parameter.
    """
    # Use provided params or fall back to settings
    subdomain = subdomain or settings.nexhealth_subdomain

    if not subdomain:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain. Provide as query param or configure in settings.",
        )

    params: dict[str, Any] = {
        "subdomain": subdomain,
        "page": page,
        "per_page": per_page,
    }

    if location_id:
        params["location_id"] = location_id
    if ids:
        params["ids[]"] = ids
    if foreign_id:
        params["foreign_id"] = foreign_id
    if requestable is not None:
        params["requestable"] = requestable
    if inactive is not None:
        params["inactive"] = inactive
    if updated_since:
        params["updated_since"] = updated_since
    if include:
        params["include[]"] = include

    return await handle_nexhealth_request(client, "GET", "/providers", params=params)


@router.get("/providers/{id}", response_model=ProviderDetailResponse)
@limiter.limit(RATE_READ)
async def get_provider(
    request: Request,
    id: int = Path(..., description="The NexHealth id of the Provider"),
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    include: list[str] | None = Query(None, alias="include[]", description="Resources to include (locations, availabilities, appointment_types)"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """View provider."""
    subdomain = subdomain or settings.nexhealth_subdomain

    if not subdomain:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain. Provide as query param or configure in settings.",
        )

    params: dict[str, Any] = {"subdomain": subdomain}
    if include:
        params["include[]"] = include

    return await handle_nexhealth_request(client, "GET", f"/providers/{id}", params=params)
