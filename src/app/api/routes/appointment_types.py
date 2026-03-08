"""Appointment Types routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from src.app.api.helpers import handle_nexhealth_request
from src.app.api.models import (
    AppointmentTypeDetailResponse,
    AppointmentTypeListResponse,
    CreateAppointmentTypeRequest,
    EmrApptDescriptorListResponse,
    UpdateAppointmentTypeRequest,
)
from src.app.api.deps import get_current_admin
from src.app.config import Settings, get_settings
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.nexhealth.client import NexHealthClient
from src.app.api.rate_limit import limiter, RATE_READ, RATE_WRITE

router = APIRouter(dependencies=[Depends(get_current_admin)])


@router.get("/appointment_types", response_model=AppointmentTypeListResponse)
@limiter.limit(RATE_READ)
async def list_appointment_types(
    request: Request,
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


@router.post("/appointment_types", response_model=AppointmentTypeDetailResponse, status_code=201)
@limiter.limit(RATE_WRITE)
async def create_appointment_type(
    request: Request,
    body: CreateAppointmentTypeRequest,
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    Create an appointment type.

    Use this to define a new type of appointment (e.g., "New Patient Exam", "Cleaning").
    Link to EMR descriptors using emr_appt_descriptor_ids to map to PMS procedure codes.

    Example:
    ```json
    {
        "location_id": 123,
        "appointment_type": {
            "name": "Adult Cleaning",
            "minutes": 45,
            "bookable_online": true,
            "emr_appt_descriptor_ids": [1822, 1823]
        }
    }
    ```
    """
    subdomain = subdomain or settings.nexhealth_subdomain

    if not subdomain:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain. Provide as query param or configure in settings.",
        )

    params: dict[str, Any] = {"subdomain": subdomain}

    # Build request body
    json_body: dict[str, Any] = {
        "appointment_type": body.appointment_type.model_dump(exclude_none=True)
    }

    if body.location_id:
        json_body["location_id"] = body.location_id

    return await handle_nexhealth_request(client, "POST", "/appointment_types", params=params, json=json_body)


@router.get("/appointment_types/{id}", response_model=AppointmentTypeDetailResponse)
@limiter.limit(RATE_READ)
async def get_appointment_type(
    request: Request,
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


@router.patch("/appointment_types/{id}", response_model=AppointmentTypeDetailResponse)
@limiter.limit(RATE_WRITE)
async def update_appointment_type(
    request: Request,
    body: UpdateAppointmentTypeRequest,
    id: int = Path(..., description="The appointment type ID"),
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    location_id: int | None = Query(None, description="Id of the associated location, required when appointment types are location specific"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    Update an appointment type.

    Use this to modify appointment type properties or update linked EMR descriptors.

    Example:
    ```json
    {
        "appointment_type": {
            "name": "Adult Prophy",
            "minutes": 60,
            "emr_appt_descriptor_ids": [1822, 1823, 1824]
        }
    }
    ```
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

    json_body: dict[str, Any] = {
        "appointment_type": body.appointment_type.model_dump(exclude_none=True)
    }

    return await handle_nexhealth_request(client, "PATCH", f"/appointment_types/{id}", params=params, json=json_body)


@router.delete("/appointment_types/{id}", response_model=AppointmentTypeDetailResponse)
@limiter.limit(RATE_WRITE)
async def delete_appointment_type(
    request: Request,
    id: int = Path(..., description="The appointment type ID"),
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    location_id: int | None = Query(None, description="Id of the associated location, required when appointment types are location specific"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    Delete an appointment type.

    This removes the appointment type from the system. Use with caution as this
    may affect existing schedules and booking configurations.
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

    return await handle_nexhealth_request(client, "DELETE", f"/appointment_types/{id}", params=params)


@router.get("/appointment_types/{id}/appointment_descriptors", response_model=EmrApptDescriptorListResponse)
@limiter.limit(RATE_READ)
async def get_appointment_type_descriptors(
    request: Request,
    id: int = Path(..., description="The appointment type ID"),
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    location_id: int | None = Query(None, description="Id of the associated location, required when appointment types are location specific"),
    descriptor_type: str | None = Query(None, description="Filter by descriptor type (e.g., 'Procedure Code')"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    View appointment type appointment descriptors.

    Returns the EMR/PMS descriptors (procedure codes, appointment types from EHR)
    that are linked to this appointment type.

    Supported integrations:
    - Procedure codes: Dentrix, Dentrix Ascend, Dentrix Enterprise, Denticon, Eaglesoft, Open Dental
    - EHR-specific appointment types: Athenahealth, Dentrix, Dentrix Enterprise, Eaglesoft, Open Dental
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
    if descriptor_type:
        params["descriptor_type"] = descriptor_type

    return await handle_nexhealth_request(
        client, "GET", f"/appointment_types/{id}/appointment_descriptors", params=params
    )
