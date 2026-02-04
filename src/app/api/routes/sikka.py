"""Sikka API routes."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from src.app.api.routes.base import verify_admin_key
from src.app.dependencies import get_sikka_client_dependency
from src.app.sikka.client import SikkaClient
from src.app.sikka.exceptions import (
    SikkaAPIError,
    SikkaAuthenticationError,
    SikkaError,
    SikkaRateLimitError,
    SikkaResourceNotFoundError,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Sikka Request Helper (DRY error handling)
# =============================================================================


async def handle_sikka_request(
    client: SikkaClient,
    method: str,
    path: str,
    office_id: str,
    secret_key: str,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """
    Handle Sikka API request with consistent error handling.

    Args:
        client: Sikka client instance
        method: HTTP method
        path: API path
        office_id: Practice office ID
        secret_key: Practice secret key
        params: Query parameters
        json: JSON body

    Returns:
        API response payload

    Raises:
        HTTPException: With appropriate status code
    """
    try:
        return await client.request(
            method, path, params=params, json=json, office_id=office_id, secret_key=secret_key
        )
    except SikkaAuthenticationError as e:
        logger.error(f"Sikka authentication error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Sikka authentication failed: {e.short_message or str(e)}",
        ) from e
    except SikkaRateLimitError as e:
        logger.warning(f"Sikka rate limit: {e}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Retry after {e.retry_after}s" if e.retry_after else "Rate limit exceeded",
        ) from e
    except SikkaResourceNotFoundError as e:
        logger.warning(f"Sikka resource not found: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except SikkaAPIError as e:
        logger.error(f"Sikka API error: {e}")
        # Sikka returns http_code as string, ensure it's int for FastAPI
        status_code = int(e.http_code) if e.http_code else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(
            status_code=status_code,
            detail=f"Sikka API error: {str(e)}",
        ) from e
    except SikkaError as e:
        logger.error(f"Sikka error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


# =============================================================================
# Response Models
# =============================================================================

class AuthorizedPractice(BaseModel):
    """Authorized practice from Sikka."""

    office_id: str
    secret_key: str
    # Additional fields that may be returned
    practice_name: str | None = None
    domain: str | None = None


class AuthorizedPracticesResponse(BaseModel):
    """Response for authorized practices endpoint."""

    practices: list[dict[str, Any]]
    count: int


class RequestKeyResponse(BaseModel):
    """Response for request key generation."""

    request_key: str
    expires_in: str
    status: str
    office_id: str


class OAuthCallbackResponse(BaseModel):
    """Response for OAuth callback."""

    status: str
    message: str


# =============================================================================
# Patient Models
# =============================================================================


class SikkaPatient(BaseModel):
    """Sikka patient record - standardized fields."""

    id: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    fullname: str | None = None
    email: str | None = None
    cell: str | None = None
    homephone: str | None = None
    workphone: str | None = None
    dob: str | None = None
    gender: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    status: str | None = None
    guarantor_id: str | None = None
    provider_id: str | None = None
    last_visit_date: str | None = None
    first_visit_date: str | None = None


class SikkaPatientListResponse(BaseModel):
    """Response for patient list endpoint."""

    patients: list[dict[str, Any]]
    count: int


class SikkaPatientDetailResponse(BaseModel):
    """Response for single patient lookup."""

    patient: dict[str, Any] | None
    found: bool


# =============================================================================
# Public Routes (OAuth callback - no auth required)
# =============================================================================

public_router = APIRouter()


@public_router.get("/oauth/callback", response_model=OAuthCallbackResponse)
async def oauth_callback(
    request: Request,
    office_id: Annotated[str | None, Query(description="Practice office ID")] = None,
    status_param: Annotated[str | None, Query(alias="status", description="Installation status")] = None,
) -> OAuthCallbackResponse:
    """
    OAuth callback endpoint for Sikka Marketplace app installation.

    When a dental practice clicks "Install" on your app in the Sikka Marketplace,
    Sikka redirects them to this URL to complete setup.

    This endpoint receives the installation confirmation and can be used to:
    - Log the new installation
    - Trigger any post-installation setup
    - Display a success message to the practice user
    """
    # Log installation (no PHI - just office_id)
    logger.info(f"Sikka OAuth callback received: office_id={office_id}, status={status_param}")

    # Get all query params for debugging (in non-production)
    all_params = dict(request.query_params)
    logger.debug(f"OAuth callback params: {all_params}")

    if status_param == "success" or office_id:
        return OAuthCallbackResponse(
            status="success",
            message="Application installed successfully. You can now close this window.",
        )

    return OAuthCallbackResponse(
        status="pending",
        message="Installation in progress. Please complete the setup in Sikka Marketplace.",
    )


# =============================================================================
# Admin Routes (require admin API key)
# =============================================================================

router = APIRouter(dependencies=[Depends(verify_admin_key)])


@router.get("/authorized_practices", response_model=AuthorizedPracticesResponse)
async def get_authorized_practices(
    client: Annotated[SikkaClient | None, Depends(get_sikka_client_dependency)],
) -> AuthorizedPracticesResponse:
    """
    Get list of practices that have authorized this application.

    Returns the master list of all practices that have installed the app
    and for which you have valid credentials (office_id and secret_key).
    """
    if not client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sikka client not configured. Check SIKKA_APP_ID and SIKKA_APP_SECRET.",
        )

    try:
        practices = await client.get_authorized_practices()
        return AuthorizedPracticesResponse(
            practices=practices,
            count=len(practices),
        )
    except SikkaAuthenticationError as e:
        logger.error(f"Sikka authentication failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Sikka authentication failed: {e.short_message or str(e)}",
        )
    except SikkaError as e:
        logger.error(f"Sikka API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sikka API error: {str(e)}",
        )


@router.post("/request_key", response_model=RequestKeyResponse)
async def generate_request_key(
    client: Annotated[SikkaClient | None, Depends(get_sikka_client_dependency)],
    office_id: Annotated[str, Query(description="Practice office ID from authorized_practices")],
    secret_key: Annotated[str, Query(description="Practice secret key from authorized_practices")],
) -> RequestKeyResponse:
    """
    Generate a request key (session token) for a specific practice.

    Use the office_id and secret_key from the authorized_practices response
    to generate a request_key that can be used for subsequent API calls.

    The request_key is valid for approximately 24 hours.
    """
    if not client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sikka client not configured. Check SIKKA_APP_ID and SIKKA_APP_SECRET.",
        )

    try:
        # Use the auth service directly to get the request key
        if not client._auth_service:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Sikka client not properly initialized",
            )

        request_key, expires_in = await client._auth_service.generate_request_key(
            office_id=office_id,
            secret_key=secret_key,
        )

        return RequestKeyResponse(
            request_key=request_key,
            expires_in=f"{expires_in} seconds",
            status="active",
            office_id=office_id,
        )
    except SikkaAuthenticationError as e:
        logger.error(f"Sikka request key generation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Failed to generate request key: {e.short_message or str(e)}",
        )
    except SikkaError as e:
        logger.error(f"Sikka API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sikka API error: {str(e)}",
        )


# =============================================================================
# Patient Routes
# =============================================================================


@router.get("/patients", response_model=SikkaPatientListResponse)
async def list_patients(
    client: Annotated[SikkaClient | None, Depends(get_sikka_client_dependency)],
    office_id: Annotated[str, Query(description="Practice office ID")],
    secret_key: Annotated[str, Query(description="Practice secret key")],
    # Search/Filter parameters
    firstname: Annotated[str | None, Query(description="Filter by first name")] = None,
    lastname: Annotated[str | None, Query(description="Filter by last name")] = None,
    cell: Annotated[str | None, Query(description="Filter by cell phone (recommended for unique match)")] = None,
    email: Annotated[str | None, Query(description="Filter by email")] = None,
    phone: Annotated[str | None, Query(description="Filter by any phone number")] = None,
    homephone: Annotated[str | None, Query(description="Filter by home phone")] = None,
    workphone: Annotated[str | None, Query(description="Filter by work phone")] = None,
    search: Annotated[
        str | None,
        Query(description="Search patient by firstname, lastname, email, middlename, city, state, zipcode, phones"),
    ] = None,
    patient_id: Annotated[str | None, Query(description="Filter by patient ID in PMS")] = None,
    provider_id: Annotated[str | None, Query(description="Filter by provider ID")] = None,
    guarantor_id: Annotated[str | None, Query(description="Filter by guarantor ID")] = None,
    status_filter: Annotated[str | None, Query(alias="status", description="Filter by patient status")] = None,
    first_visit: Annotated[str | None, Query(description="Filter by first visit date")] = None,
    last_visit: Annotated[str | None, Query(description="Filter by last visit date")] = None,
    # Sorting
    sort_by: Annotated[
        str | None,
        Query(alias="sortby", description="Sort by: patient_id (default), firstname, lastname, zipcode, rowhash"),
    ] = None,
    # Pagination
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Results per page (max 100)")] = 25,
) -> SikkaPatientListResponse:
    """
    List/search patients in a Sikka-connected practice.

    Best practices:
    - Always filter by cell phone first for unique matches (Caller ID feature)
    - Use 'search' param for broad searches across multiple fields
    - Keep result sets small for performance

    Note: For Tracker PMS, data syncs daily. New patients may not appear immediately.
    """
    if not client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sikka client not configured. Check SIKKA_APP_ID and SIKKA_APP_SECRET.",
        )

    # Build query params
    params: dict[str, Any] = {}

    # Search/filter params
    if firstname:
        params["firstname"] = firstname
    if lastname:
        params["lastname"] = lastname
    if cell:
        params["cell"] = cell
    if email:
        params["email"] = email
    if phone:
        params["phone"] = phone
    if homephone:
        params["homephone"] = homephone
    if workphone:
        params["workphone"] = workphone
    if search:
        params["search"] = search
    if patient_id:
        params["patient_id"] = patient_id
    if provider_id:
        params["provider_id"] = provider_id
    if guarantor_id:
        params["guarantor_id"] = guarantor_id
    if status_filter:
        params["status"] = status_filter
    if first_visit:
        params["first_visit"] = first_visit
    if last_visit:
        params["last_visit"] = last_visit
    if sort_by:
        params["sortby"] = sort_by

    # Pagination - Sikka uses 'page' and 'pagesize'
    params["page"] = page
    params["pagesize"] = per_page

    response = await handle_sikka_request(
        client, "GET", "/patients", office_id=office_id, secret_key=secret_key, params=params
    )

    # Sikka returns array directly or paginated with 'items'
    if isinstance(response, list):
        patients = response
    elif isinstance(response, dict):
        patients = response.get("items", response.get("data", []))
    else:
        patients = []

    return SikkaPatientListResponse(patients=patients, count=len(patients))


@router.get("/patients/{patient_id}", response_model=SikkaPatientDetailResponse)
async def get_patient(
    client: Annotated[SikkaClient | None, Depends(get_sikka_client_dependency)],
    patient_id: str,
    office_id: Annotated[str, Query(description="Practice office ID")],
    secret_key: Annotated[str, Query(description="Practice secret key")],
) -> SikkaPatientDetailResponse:
    """
    Get a specific patient by ID.

    Note: patient_id is the PMS-specific patient ID.
    """
    if not client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sikka client not configured.",
        )

    params = {"patient_id": patient_id}

    response = await handle_sikka_request(
        client, "GET", "/patients", office_id=office_id, secret_key=secret_key, params=params
    )

    # Parse response
    if isinstance(response, list) and len(response) > 0:
        return SikkaPatientDetailResponse(patient=response[0], found=True)
    elif isinstance(response, dict):
        items = response.get("items", response.get("data", []))
        if items and len(items) > 0:
            return SikkaPatientDetailResponse(patient=items[0], found=True)

    return SikkaPatientDetailResponse(patient=None, found=False)