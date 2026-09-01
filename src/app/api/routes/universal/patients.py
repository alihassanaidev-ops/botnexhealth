"""Universal patient endpoints."""

from datetime import datetime, timezone
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.app.api.deps import get_current_institution_or_location_user
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.database import get_db_session
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.models.contact import Contact
from src.app.models.contact_location_access import ContactLocationAccess
from src.app.models.user import User, UserRole
from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_institution_pms
from src.app.pms.models import PatientCreateRequest, UniversalPatient
from src.app.services.audit_decorator import audit
from src.app.services.sms_privacy import mask_phone

router = APIRouter(prefix="/patients", tags=["Patients"])
logger = logging.getLogger(__name__)


class PatientDirectoryItem(BaseModel):
    pms_patient_id: str
    source: str
    first_name: str
    last_name: str
    full_name: str
    inactive: bool = False
    email: str | None = None
    phone: str | None = None
    email_masked: str | None = None
    phone_masked: str | None = None
    contact_details_masked: bool = False
    can_reveal_contact_details: bool = False
    pms_updated_at: str | None = None
    pms_last_sync_time: str | None = None
    contact_id: str | None = None


class PatientDirectoryPage(BaseModel):
    source: str
    fetched_at: str
    total: int | None = None
    returned: int = 0
    items: list[PatientDirectoryItem] = Field(default_factory=list)
    next_cursor: str | None = None
    previous_cursor: str | None = None
    has_next_page: bool = False
    has_previous_page: bool = False


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return f"{local[:1]}{'*' * max(len(local) - 1, 1)}@{domain}"


async def _local_contact_ids(
    *, current_user: User, pms_patient_ids: list[str]
) -> dict[str, str]:
    """Resolve only locally visible Contact links for this bounded page."""
    if not current_user.institution_id or not pms_patient_ids:
        return {}
    stmt = select(Contact.nexhealth_patient_id, Contact.id).where(
        Contact.institution_id == current_user.institution_id,
        Contact.nexhealth_patient_id.in_(pms_patient_ids),
        Contact.merged_into_id.is_(None),
    )
    if current_user.location_id:
        stmt = stmt.where(
            Contact.id.in_(
                select(ContactLocationAccess.contact_id).where(
                    ContactLocationAccess.location_id == str(current_user.location_id)
                )
            )
        )
    async with get_db_session() as session:
        rows = (await session.execute(stmt)).all()
    return {str(pms_id): str(contact_id) for pms_id, contact_id in rows if pms_id}


def _patient_directory_resource(*_args, **kwargs) -> str:
    patient_status = kwargs.get("patient_status") or "active"
    operation = "reveal" if kwargs.get("reveal_patient_id") else "list"
    return f"patient_directory:{patient_status}:{operation}"


@router.get("/page", response_model=PatientDirectoryPage)
@limiter.limit(RATE_READ)
@audit(
    AuditAction.SEARCH_PATIENTS,
    resource=_patient_directory_resource,
    actor=AuditActor.ADMIN,
)
async def browse_patients(
    request: Request,
    current_user: Annotated[User, Depends(get_current_institution_or_location_user)],
    cursor: str | None = Query(None, max_length=1024),
    page_size: int = Query(25, ge=1, le=100),
    search: str | None = Query(None, min_length=2, max_length=128),
    patient_status: Literal["active", "inactive", "all"] = Query("active"),
    reveal_patient_id: str | None = Query(None, min_length=1, max_length=128),
    pms: PMSAdapter = Depends(get_institution_pms),
) -> PatientDirectoryPage:
    """Read one current patient page from NexHealth or GoTracker.

    Location-scoped clinic users receive contact fields directly. Institution
    admins receive masked contact fields unless they explicitly reveal one row;
    that second bounded read is separately identifiable in the audit resource.
    """
    try:
        page = await pms.browse_patients(
            cursor=cursor,
            page_size=page_size,
            name=search.strip() if search else None,
            status=patient_status,
        )
        contact_ids = await _local_contact_ids(
            current_user=current_user,
            pms_patient_ids=[patient.id for patient in page.items],
        )
        institution_admin = current_user.role == UserRole.INSTITUTION_ADMIN.value
        items = []
        for patient in page.items:
            reveal_this_patient = institution_admin and reveal_patient_id == patient.id
            show_contact_details = not institution_admin or reveal_this_patient
            items.append(
                PatientDirectoryItem(
                    pms_patient_id=patient.id,
                    source=patient.source,
                    first_name=patient.first_name,
                    last_name=patient.last_name,
                    full_name=(
                        " ".join(
                            value
                            for value in (patient.first_name, patient.last_name)
                            if value
                        ).strip()
                        or "Unknown patient"
                    ),
                    inactive=bool(patient.extra.get("inactive", False)),
                    email=patient.email if show_contact_details else None,
                    phone=patient.phone if show_contact_details else None,
                    email_masked=_mask_email(patient.email),
                    phone_masked=mask_phone(patient.phone),
                    contact_details_masked=not show_contact_details,
                    can_reveal_contact_details=(
                        institution_admin and not reveal_this_patient
                    ),
                    pms_updated_at=_string_or_none(patient.extra.get("updated_at")),
                    pms_last_sync_time=_string_or_none(
                        patient.extra.get("last_sync_time")
                    ),
                    contact_id=contact_ids.get(patient.id),
                )
            )
        return PatientDirectoryPage(
            source=pms.source,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            total=page.total,
            returned=len(items),
            items=items,
            next_cursor=page.next_cursor,
            previous_cursor=page.previous_cursor,
            has_next_page=page.has_next_page,
            has_previous_page=page.has_previous_page,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The patient page cursor or filter is invalid.",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - provider failures become a stable API response.
        location = getattr(request.state, "location", None)
        logger.warning(
            "PMS patient page failed source=%s location=%s type=%s",
            getattr(pms, "source", "unknown"),
            getattr(location, "id", None),
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The practice system is temporarily unavailable. Try again shortly.",
        ) from exc
    finally:
        try:
            await pms.close()
        except Exception as exc:  # noqa: BLE001 - cleanup must not replace the response.
            logger.warning(
                "PMS patient page adapter cleanup failed source=%s type=%s",
                getattr(pms, "source", "unknown"),
                type(exc).__name__,
            )


def _string_or_none(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


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
    """Prevent broad patient enumeration.

    Email and phone are unique identifiers — either alone is sufficient.
    Date of birth is a supporting identifier only: hundreds of patients
    share any given DOB, so DOB-only would enable enumeration of full
    PHI by date. DOB must be combined with a name (q >= 2 chars) or a
    unique identifier.
    """
    search_text = q.strip()
    has_unique_identifier = any(
        value and value.strip() for value in (email, phone_number)
    )
    has_name = len(search_text) >= 2
    has_dob = bool(date_of_birth and date_of_birth.strip())

    if has_unique_identifier or has_name:
        return search_text
    if has_dob:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Date of birth alone is not sufficient. Combine with a name "
                "(at least 2 characters) or an email/phone number."
            ),
        )
    if search_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient search requires at least 2 characters or an email/phone number.",
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Patient search requires a name, email, or phone number.",
    )


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
