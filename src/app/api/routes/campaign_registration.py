"""Turn a campaign lead into a patient record in the practice software.

A campaign aimed at enquiries mostly targets contacts with no PMS record. Every
booking link for one of those falls through to a staff handoff: the intent is
captured, but nothing self-books. This is the step that closes that gap.

``PatientCreateRequest`` demands seven fields. Four — both names, email and
phone — are already on the contact the campaign enrolled. ``provider_id`` is a
clinic decision and comes from the ``patient_registration`` step's config. That
leaves **date of birth and gender**, which only the patient can supply, so this
is a short form rather than a silent conversion.

Creating real records in a clinic's practice software from an unauthenticated
web form is deliberate, so:

* the ``register`` action has no merge-field placeholder — only a
  ``patient_registration`` step issues one;
* a run whose contact already has a PMS id is refused rather than duplicated;
* the same ``Referrer-Policy: no-referrer`` rules apply as every other action
  link, because the token travels in the patient's browser.

The reply never states whether a matching record already existed. Enumerating a
clinic's patient list through a public form is exactly what that would allow.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.app.database import get_system_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.services.audit import log_audit_background
from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.campaign_response import CampaignResponseEvent
from src.app.models.contact import Contact
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.pms.models import PatientCreateRequest
from src.app.pms.factory import get_adapter_for_institution_location
from src.app.services.automation.campaign_action_links import (
    EXPIRED,
    INVALID,
    LINK_RESPONSE_HEADERS,
    REGISTRATION_CONFIG_KEY,
    verify_action_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns/link", tags=["Campaign Links"])

ACTION = "register"

#: YYYY-MM-DD, the format every adapter forwards to its PMS unchanged.
_DOB_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The PMS contract is a closed set; anything else is rejected before the call.
GENDERS = ("Female", "Male", "Other")


class RegistrationDetails(BaseModel):
    """What the form shows before the patient fills it in."""

    clinic_name: str = ""
    first_name: str = ""
    last_name: str = ""
    #: Prefilled and editable — a transcribed address is often wrong, and it is
    #: the address the clinic will use from then on.
    email: str = ""
    phone: str = ""
    #: True once a PMS record exists, so the page says so instead of asking
    #: the patient to fill a form that will be refused.
    already_registered: bool = False


class RegistrationRequest(BaseModel):
    date_of_birth: str = Field(..., description="YYYY-MM-DD")
    gender: str
    #: Corrections to what the campaign held. Absent means "keep what we have".
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None


def _json(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        payload, status_code=status_code, headers=dict(LINK_RESPONSE_HEADERS)
    )


def _verify(token: str) -> str | JSONResponse:
    """Check the token is a valid, unexpired registration token."""
    verified = verify_action_token(token)
    if verified == EXPIRED:
        return _json({"error": "expired"}, 410)
    if verified == INVALID:
        return _json({"error": "invalid"}, 400)
    run_id, token_action = verified  # type: ignore[misc]
    if token_action != ACTION:
        # A booking token must not register a patient: the action is inside the
        # signed payload precisely so one link cannot be edited into another.
        return _json({"error": "invalid"}, 400)
    return run_id


def _provider_id(run: AutomationWorkflowRun) -> str | None:
    config = (run.trigger_metadata or {}).get(REGISTRATION_CONFIG_KEY)
    if not isinstance(config, dict):
        return None
    provider_id = config.get("provider_id")
    return str(provider_id) if provider_id else None


def _clean(value: str | None) -> str:
    return (value or "").strip()


@router.get("/register/details")
async def registration_details(
    token: str = Query(..., description="Signed per-run action token"),
) -> JSONResponse:
    """Prefill the form from what the campaign already knows."""
    verified = _verify(token)
    if isinstance(verified, JSONResponse):
        return verified
    run_id = verified

    async with get_system_db_session(
        "campaign_booking_link", external_id=run_id
    ) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)
        location = (
            await session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        contact = (
            await session.get(Contact, run.contact_id) if run.contact_id else None
        )
        if location is None or contact is None:
            return _json({"error": "unavailable"}, 503)

        return _json(
            RegistrationDetails(
                clinic_name=getattr(location, "name", "") or "",
                first_name=_clean(contact.first_name),
                last_name=_clean(contact.last_name),
                email=_clean(contact.email),
                phone=_clean(contact.phone),
                already_registered=bool(contact.nexhealth_patient_id),
            ).model_dump()
        )


@router.post("/register")
async def register_patient(
    body: RegistrationRequest,
    token: str = Query(..., description="Signed per-run action token"),
) -> JSONResponse:
    """Create the patient in the practice software and link the contact."""
    verified = _verify(token)
    if isinstance(verified, JSONResponse):
        return verified
    run_id = verified

    date_of_birth = _clean(body.date_of_birth)
    if not _DOB_RE.match(date_of_birth):
        return _json({"error": "invalid_date_of_birth"}, 400)
    gender = _clean(body.gender)
    if gender not in GENDERS:
        return _json({"error": "invalid_gender"}, 400)

    async with get_system_db_session(
        "campaign_booking_link", external_id=run_id
    ) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)

        institution = await session.get(Institution, run.institution_id)
        location = (
            await session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        contact = (
            await session.get(Contact, run.contact_id) if run.contact_id else None
        )
        if institution is None or location is None or contact is None:
            return _json({"error": "unavailable"}, 503)

        if contact.nexhealth_patient_id:
            # Reopening the link must not create a second record for the same
            # person. Idempotent by the same reasoning as already_booked.
            return _json({"status": "already_registered"})

        provider_id = _provider_id(run)
        if not provider_id:
            # The step is what names the provider a self-registered patient is
            # filed under. Without it there is nothing safe to guess.
            logger.warning("registration link has no provider configured run=%s", run_id)
            return _json({"error": "unavailable"}, 503)

        first_name = _clean(body.first_name) or _clean(contact.first_name)
        last_name = _clean(body.last_name) or _clean(contact.last_name)
        email = _clean(body.email) or _clean(contact.email)
        phone = _clean(body.phone) or _clean(contact.phone)
        if not (first_name and last_name and email and phone):
            return _json({"error": "missing_details"}, 400)

        try:
            adapter = await get_adapter_for_institution_location(institution, location)
            result: dict[str, Any] = await adapter.create_patient(
                PatientCreateRequest(
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    phone=phone,
                    date_of_birth=date_of_birth,
                    provider_id=provider_id,
                    gender=gender,  # type: ignore[arg-type]
                )
            )
        except Exception:
            # Never echo the PMS error: it routinely repeats the submitted
            # payload, which is this patient's own PHI.
            logger.exception("patient registration failed for run=%s", run_id)
            return _json({"error": "unavailable"}, 503)

        patient_id = result.get("patient_id")
        if not result.get("success") or not patient_id:
            logger.warning(
                "practice software refused patient creation run=%s", run_id
            )
            return _json({"error": "could_not_register"}, 502)

        # A new record in the clinic's practice software, created from a public
        # form. If anything in this system deserves a durable trace, it is this.
        log_audit_background(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.CREATE_PATIENT,
            target_resource=f"campaign_run:{run.id}:register",
            outcome=AuditOutcome.SUCCESS,
            institution_id=str(run.institution_id),
            location_id=str(run.location_id) if run.location_id else None,
            metadata={"source": "campaign_registration_link"},
        )

        contact.nexhealth_patient_id = str(patient_id)
        if not contact.date_of_birth:
            contact.date_of_birth = date_of_birth

        session.add(
            CampaignResponseEvent(
                id=str(uuid4()),
                institution_id=str(run.institution_id),
                location_id=str(run.location_id) if run.location_id else None,
                workflow_id=str(run.workflow_id) if run.workflow_id else None,
                workflow_run_id=str(run.id),
                contact_id=str(run.contact_id) if run.contact_id else None,
                channel="booking_link",
                normalized_intent="registered",
                source="campaign_registration_link",
                source_event_id=f"link:{run.id}:registered",
                confidence="deterministic",
            )
        )
        await session.flush()

    return _json({"status": "registered"})
