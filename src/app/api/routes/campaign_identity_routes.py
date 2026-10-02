"""The identity step a patient passes through before a link will act.

What we know when someone follows a campaign link is only how we reached them:
a phone number if it came by SMS, an email address if it came by email, and on
an external form, neither. Everything else about the person is unproven.

So the page asks two things. Whether they are new or existing, which they
answer themselves rather than having us guess; and then, for an existing
patient, the same factors the voice agent asks for — name, date of birth, and a
phone or email that matches the record.

Three properties are load-bearing:

* **Nothing is prefilled that the patient is meant to be proving.** We could
  populate the phone number from the contact the campaign targeted, and it
  would be friendlier — but then the only unproven thing they supply is a date
  of birth, and the gate quietly becomes single-factor. They know their own
  number; typing it is what makes it evidence.
* **One search, one answer.** Exactly one match proceeds. No match, several
  matches and a wrong date of birth are indistinguishable from out here.
* **Attempts are capped, and running out fetches a human** rather than showing
  a wall. A patient who cannot get through is a booking the clinic never hears
  about.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.app.database import get_campaign_link_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.services.audit import log_audit_background
from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.campaign_response import (
    CampaignResponseEvent,
    CampaignStaffHandoff,
)
from src.app.models.contact import Contact
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.pms.factory import get_adapter_for_institution_location
from src.app.services.automation.campaign_action_links import (
    EXPIRED,
    INVALID,
    LINK_RESPONSE_HEADERS,
    verify_action_token,
)
from src.app.services.automation.campaign_identity import (
    MAX_ATTEMPTS,
    attempts_used,
    is_locked,
    is_verified,
    verify_identity,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns/link", tags=["Campaign Links"])

#: The single message every failed identification receives. Wording differences
#: between "no such patient", "several matched" and "wrong date of birth" are
#: precisely what turns this page into a way to test guesses.
NOT_MATCHED_MESSAGE = (
    "We couldn't match those details. Check the spelling of the name and the "
    "date of birth, and add your email address if you have one on file with us."
)

#: Shown once the attempts are used up. Differing here is safe: it describes
#: this session, not whether any patient record exists.
LOCKED_MESSAGE = (
    "We couldn't match those details. Please call the clinic and we'll sort it "
    "out — we've let the team know you tried."
)


class IdentityContext(BaseModel):
    """What the page needs to render, and nothing about the person."""

    clinic_name: str = ""
    #: Which channel reached them. The page uses it for wording ("the number we
    #: texted"), never to prefill the field itself.
    arrived_by: str = "unknown"  # "sms" | "email" | "unknown"
    #: True once this run is past the gate, so a reopened link skips straight on.
    verified: bool = False
    attempts_remaining: int = MAX_ATTEMPTS


class IdentityRequest(BaseModel):
    full_name: str = Field(..., min_length=1)
    date_of_birth: str = Field(..., description="YYYY-MM-DD")
    phone: str | None = None
    #: Optional, and it narrows rather than widens — it is added to the same
    #: search, never run as a competing second one.
    email: str | None = None


def _json(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        payload, status_code=status_code, headers=dict(LINK_RESPONSE_HEADERS)
    )


def _verify_token(token: str) -> tuple[str, str] | JSONResponse:
    verified = verify_action_token(token)
    if verified == EXPIRED:
        return _json({"error": "expired"}, 410)
    if verified == INVALID:
        return _json({"error": "invalid"}, 400)
    return verified  # type: ignore[return-value]


def _arrived_by(contact: Contact | None) -> str:
    """How the campaign reached this person — not proof of anything."""
    if contact is None:
        return "unknown"
    if contact.phone:
        return "sms"
    if contact.email:
        return "email"
    return "unknown"


@router.get("/identify/context")
async def identity_context(
    token: str = Query(..., description="Signed per-run action token"),
) -> JSONResponse:
    """What the page renders before the patient types anything."""
    verified = _verify_token(token)
    if isinstance(verified, JSONResponse):
        return verified
    run_id, _action = verified

    async with get_campaign_link_db_session(run_id) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)
        location = (
            await session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        contact = await session.get(Contact, run.contact_id) if run.contact_id else None
        return _json(
            IdentityContext(
                clinic_name=getattr(location, "name", "") or "",
                arrived_by=_arrived_by(contact),
                verified=is_verified(run),
                attempts_remaining=max(0, MAX_ATTEMPTS - attempts_used(run)),
            ).model_dump()
        )


@router.post("/identify")
async def identify(
    body: IdentityRequest,
    request: Request,
    token: str = Query(..., description="Signed per-run action token"),
) -> JSONResponse:
    """Resolve the supplied details to exactly one patient, or to nothing."""
    verified = _verify_token(token)
    if isinstance(verified, JSONResponse):
        return verified
    run_id, action = verified

    async with get_campaign_link_db_session(run_id) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)

        if is_verified(run):
            # Reopening the link after passing must not spend another attempt.
            return _json({"status": "verified"})

        if is_locked(run):
            return _json({"status": "locked", "message": LOCKED_MESSAGE}, 429)

        institution = await session.get(Institution, run.institution_id)
        location = (
            await session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        contact = await session.get(Contact, run.contact_id) if run.contact_id else None
        if institution is None or location is None:
            return _json({"error": "unavailable"}, 503)

        try:
            adapter = await get_adapter_for_institution_location(institution, location)
        except Exception:
            logger.exception("identity adapter unavailable run=%s", run_id)
            return _json({"error": "unavailable"}, 503)

        outcome = await verify_identity(
            adapter,
            full_name=body.full_name.strip(),
            date_of_birth=(body.date_of_birth or "").strip(),
            phone=(body.phone or "").strip() or None,
            email=(body.email or "").strip() or None,
            run=run,
        )

        # Recorded whichever way it went. A gate that logs only its successes
        # cannot answer the question it exists for: whether somebody sat there
        # guessing at a patient's date of birth.
        log_audit_background(
            actor=AuditActor.API_CLIENT,
            action=AuditAction.READ_PATIENT,
            target_resource=f"campaign_run:{run_id}:identify",
            outcome=(AuditOutcome.SUCCESS if outcome.ok else AuditOutcome.FAILURE),
            institution_id=str(run.institution_id),
            location_id=str(run.location_id) if run.location_id else None,
            metadata={
                "source": "campaign_identity",
                "status": outcome.status,
                # The internal reason, never returned to the page.
                "reason": outcome.reason,
                "attempts_used": attempts_used(run),
            },
        )

        if outcome.ok and outcome.patient_id:
            # Bind the proven identity to the contact so the action that
            # follows books against the person who was actually present, not
            # the person the campaign guessed.
            if contact is not None and not contact.nexhealth_patient_id:
                contact.nexhealth_patient_id = outcome.patient_id
            session.add(
                CampaignResponseEvent(
                    id=str(uuid4()),
                    institution_id=str(run.institution_id),
                    location_id=str(run.location_id) if run.location_id else None,
                    workflow_id=str(run.workflow_id) if run.workflow_id else None,
                    workflow_run_id=str(run.id),
                    contact_id=str(run.contact_id) if run.contact_id else None,
                    channel="booking_link",
                    normalized_intent="identity_verified",
                    source="campaign_identity",
                    source_event_id=f"link:{run.id}:identity_verified",
                    confidence="deterministic",
                )
            )
            await session.flush()
            return _json({"status": "verified"})

        if outcome.status == "locked":
            # Out of attempts: fetch a human rather than showing a wall. The
            # reason goes to staff, never back over the wire.
            session.add(
                CampaignStaffHandoff(
                    id=str(uuid4()),
                    institution_id=str(run.institution_id),
                    location_id=str(run.location_id) if run.location_id else None,
                    workflow_id=str(run.workflow_id) if run.workflow_id else None,
                    workflow_run_id=str(run.id),
                    contact_id=str(run.contact_id) if run.contact_id else None,
                    reason="identity_unresolved",
                    status="open",
                    summary=(
                        "A patient followed a campaign link but could not be "
                        "matched to a record after several attempts. Confirm who "
                        "they are and complete the request for them."
                    ),
                )
            )
            await session.flush()
            return _json({"status": "locked", "message": LOCKED_MESSAGE}, 429)

        await session.flush()
        return _json(
            {
                "status": "not_matched",
                "message": NOT_MATCHED_MESSAGE,
                "attempts_remaining": outcome.attempts_remaining,
            }
        )
