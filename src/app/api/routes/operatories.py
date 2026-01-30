"""Operatory routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from src.app.api.helpers import handle_nexhealth_request
from src.app.api.models import OperatoryDetailResponse, OperatoryListResponse
from src.app.api.routes.base import verify_admin_key
from src.app.config import Settings
from src.app.dependencies import get_nexhealth_client_dependency, get_settings
from src.app.nexhealth.client import NexHealthClient

router = APIRouter(dependencies=[Depends(verify_admin_key)])


@router.get("/operatories", response_model=OperatoryListResponse)
async def list_operatories(
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    location_id: int | None = Query(None, description="Used to scope the request to the specified location"),
    page: int = Query(1, ge=1),
    per_page: int = Query(5, ge=1, le=300),
    search_name: str | None = Query(None, description="Optional name search filter"),
    foreign_id: str | None = Query(None, description="Query by EMR Id"),
    updated_since: str | None = Query(None, description="Query operatories updated since datetime (ISO8601)"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """View operatories."""
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
        "page": page,
        "per_page": per_page,
    }
    if search_name:
        params["search_name"] = search_name
    if foreign_id:
        params["foreign_id"] = foreign_id
    if updated_since:
        params["updated_since"] = updated_since

    return await handle_nexhealth_request(client, "GET", "/operatories", params=params)


@router.get("/operatories/{operatory_id}", response_model=OperatoryDetailResponse)
async def get_operatory(
    operatory_id: int = Path(..., description="The NexHealth id of the operatories"),
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """View operatory."""
    subdomain = subdomain or settings.nexhealth_subdomain
    
    if not subdomain:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain. Provide as query param or configure in settings.",
        )

    params = {"subdomain": subdomain}
    
    return await handle_nexhealth_request(client, "GET", f"/operatories/{operatory_id}", params=params)
