"""Institution routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request

from src.app.api.helpers import handle_nexhealth_request
from src.app.api.models import InstitutionDetailResponse, InstitutionListResponse
from src.app.api.deps import get_current_active_user
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.nexhealth.client import NexHealthClient
from src.app.api.rate_limit import limiter, RATE_READ

router = APIRouter(dependencies=[Depends(get_current_active_user)])


@router.get("/institutions", response_model=InstitutionListResponse)
@limiter.limit(RATE_READ)
async def list_institutions(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    List all institutions accessible with the current API key.

    An institution represents a logical grouping of practices and their data sources.
    All practice-based resources are members of an institution.

    Args:
        page: Page number (default: 1)
        per_page: Items per page (default: 25, max: 100)
        client: Injected NexHealth client

    Returns:
        List of institutions with their locations and metadata
    """
    params: dict[str, Any] = {
        "page": page,
        "per_page": per_page,
    }
    return await handle_nexhealth_request(client, "GET", "/institutions", params=params)


@router.get("/institutions/{institution_id}", response_model=InstitutionDetailResponse)
@limiter.limit(RATE_READ)
async def get_institution(
    request: Request,
    institution_id: int = Path(..., description="Institution ID"),
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    Get a specific institution by ID.

    Args:
        institution_id: The institution ID
        client: Injected NexHealth client

    Returns:
        Institution details including locations and configuration
    """
    return await handle_nexhealth_request(client, "GET", f"/institutions/{institution_id}")
