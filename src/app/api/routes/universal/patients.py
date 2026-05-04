"""Universal patient endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.app.api.deps import get_current_institution_or_location_user
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.user import User
from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import PatientCreateRequest, UniversalPatient
from src.app.services.audit_decorator import audit

router = APIRouter(prefix="/patients", tags=["Patients"])


def _patient_search_resource(*_args, **kwargs) -> str:
    criteria: list[str] = []
    for key, label in (
        ("q", "name"),
        ("email", "email"),
        ("phone_number", "phone"),
        ("date_of_birth", "dob"),
    ):
        if kwargs.get(key):
            criteria.append(label)
    suffix = ",".join(criteria) if criteria else "none"
    return f"patient_search:by_{suffix}"


def _validate_patient_search(
    *,
    q: str,
    email: str | None,
    phone_number: str | None,
    date_of_birth: str | None,
) -> str:
    """Prevent broad patient enumeration from an empty typeahead request."""
    search_text = q.strip()
    has_exact_identifier = any(
        value and value.strip()
        for value in (email, phone_number, date_of_birth)
    )
    if search_text and len(search_text) < 2 and not has_exact_identifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient search requires at least 2 characters or an exact identifier.",
        )
    if not search_text and not has_exact_identifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient search requires a name, email, phone number, or date of birth.",
        )
    return search_text


@router.get("", response_model=list[UniversalPatient])
@limiter.limit(RATE_READ)
@audit(
    AuditAction.SEARCH_PATIENTS,
    resource=_patient_search_resource,
    actor=AuditActor.ADMIN,
)
async def search_patients(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
    q: str = Query("", max_length=128),
    email: str | None = Query(None, max_length=320),
    phone_number: str | None = Query(None, max_length=32),
    date_of_birth: str | None = Query(None, max_length=10),
    pms: PMSAdapter = Depends(get_institution_pms),
):
    q = _validate_patient_search(
        q=q,
        email=email,
        phone_number=phone_number,
        date_of_birth=date_of_birth,
    )
    return await pms.search_patients(
        q, email=email, phone_number=phone_number, date_of_birth=date_of_birth
    )


@router.post("")
@limiter.limit(RATE_WRITE)
@audit(
    AuditAction.CREATE_PATIENT,
    resource=lambda *a, **kw: "new_patient",
    actor=AuditActor.ADMIN,
)
async def create_patient(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
    req: PatientCreateRequest,
    pms: PMSAdapter = Depends(get_institution_pms),
):
    return await pms.create_patient(req)
