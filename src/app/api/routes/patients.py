"""Patient routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from src.app.api.helpers import handle_nexhealth_request
from src.app.api.models import CreatePatientRequest, PatientDetailResponse, PatientListResponse
from src.app.api.deps import get_current_user
from src.app.config import Settings, get_settings
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.nexhealth.client import NexHealthClient
from src.app.services.audit_decorator import audit

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/patients", response_model=PatientListResponse)
@audit(
    AuditAction.SEARCH_PATIENTS, 
    resource=lambda request, subdomain, location_id, name, email, phone_number, date_of_birth, **kwargs:
        "patient_search:by_" + ",".join(
            k for k, v in [("name", name), ("email", email), ("phone", phone_number), ("dob", date_of_birth)] if v
        ),
    actor=AuditActor.API_CLIENT
)
async def list_patients(
    request: Request,
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    location_id: int | None = Query(None, description="Used to scope the request to the specified location"),
    name: str | None = Query(None, description="Patient name"),
    email: str | None = Query(None, description="Patient email"),
    phone_number: str | None = Query(None, description="Patient phone number"),
    date_of_birth: str | None = Query(None, description="Patient date of birth (YYYY-MM-DD)"),
    inactive: bool | None = Query(None),
    foreign_id: str | None = Query(None, description="Unique patient id from the EMR"),
    updated_since: str | None = Query(None, description="Only return messages created since specified date"),
    new_patient: bool | None = Query(None, description="Include new patients with an upcoming appointment"),
    non_patient: bool | None = Query(None, description="Filter non_patients"),
    forms_syncable: bool | None = Query(None, description="Filter to patients that can have forms inserted"),
    location_strict: bool | None = Query(None, description="Only returns patients belonging to the specified location"),
    include: list[str] | None = Query(None, alias="include[]", description="Resources to be included"),
    sort: str | None = Query(None, description="Sort fields"),
    appointment_date_start: str | None = Query(None, description="Include appointments starting from this date"),
    appointment_date_end: str | None = Query(None, description="Include appointments up to this date"),
    page: int = Query(1, ge=1),
    per_page: int = Query(5, ge=1, le=300),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """View patients."""
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
    
    if name: params["name"] = name
    if email: params["email"] = email
    if phone_number: params["phone_number"] = phone_number
    if date_of_birth: params["date_of_birth"] = date_of_birth
    if inactive is not None: params["inactive"] = inactive
    if foreign_id: params["foreign_id"] = foreign_id
    if updated_since: params["updated_since"] = updated_since
    if new_patient is not None: params["new_patient"] = new_patient
    if non_patient is not None: params["non_patient"] = non_patient
    if forms_syncable is not None: params["forms_syncable"] = forms_syncable
    if location_strict is not None: params["location_strict"] = location_strict
    if sort: params["sort"] = sort
    if appointment_date_start: params["appointment_date_start"] = appointment_date_start
    if appointment_date_end: params["appointment_date_end"] = appointment_date_end
    if include: params["include[]"] = include

    return await handle_nexhealth_request(client, "GET", "/patients", params=params)


@router.get("/patients/{id}", response_model=PatientDetailResponse)
@audit(
    AuditAction.READ_PATIENT, 
    resource=lambda request, id, **kwargs: f"patient:{id}",
    actor=AuditActor.API_CLIENT
)
async def get_patient(
    request: Request,
    id: int = Path(..., description="Id of the patient"),
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    include: list[str] | None = Query(None, alias="include[]", description="Resources to be included"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """View patient."""
    subdomain = subdomain or settings.nexhealth_subdomain
    
    if not subdomain:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain. Provide as query param or configure in settings.",
        )

    params: dict[str, Any] = {"subdomain": subdomain}
    if include:
        params["include[]"] = include
        
    return await handle_nexhealth_request(client, "GET", f"/patients/{id}", params=params)


@router.post("/patients")
@audit(
    AuditAction.CREATE_PATIENT, 
    resource=lambda request, body, **kwargs: "new_patient:created",
    actor=AuditActor.API_CLIENT
)
async def create_patient(
    request: Request,
    body: CreatePatientRequest,
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    location_id: int | None = Query(None, description="Used to scope the request to the specified location"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """Create a new patient."""
    subdomain = subdomain or settings.nexhealth_subdomain
    location_id = location_id or settings.nexhealth_location_id

    if not subdomain or not location_id:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain or location_id.",
        )

    params: dict[str, Any] = {
        "subdomain": subdomain,
        "location_id": location_id
    }

    return await handle_nexhealth_request(client, "POST", "/patients", params=params, json=body.model_dump())
