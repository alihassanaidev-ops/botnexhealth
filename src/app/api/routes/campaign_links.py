"""Patient-facing landing endpoints for campaign action links (Item 12).

A patient opens these straight from a text message or email with no login, so
the signed token is the only thing standing between a stranger and a clinic's
schedule. Three rules govern everything here:

* **Say nothing the patient does not already know.** No name, no appointment
  detail, no clinic-internal state. A correct token proves possession of the
  message, not identity.
* **Send no-referrer on every response.** A token in a URL otherwise reaches any
  analytics or ad script on the page through the ``Referer`` header — the
  mechanism behind the finding that most hotel booking sites leak guest booking
  references.
* **Tell an expired link apart from a forged one.** A patient whose link ran out
  can act on that; a forger learns nothing either way.

Confirming is completed here, because the write-back already exists on both
practice-software adapters. Booking and rescheduling need a slot-picking page
that does not exist yet, so they record the patient's intent and raise a staff
handoff rather than pretending to be finished.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from src.app.database import get_system_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.campaign_response import CampaignResponseEvent, CampaignStaffHandoff
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.pms.factory import get_adapter_for_institution_location
from src.app.security import get_client_ip
from src.app.services.audit import log_audit_background
from src.app.services.automation.campaign_action_links import (
    ACTIONS,
    EXPIRED,
    INVALID,
    LINK_RESPONSE_HEADERS,
    verify_action_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns/link", tags=["Campaign Links"])

_EXPIRED_TEXT = (
    "This link has expired. Please contact the clinic directly and they'll help you."
)
_INVALID_TEXT = "This link isn't valid. Please contact the clinic directly."
_GONE_TEXT = "This link is no longer active. Please contact the clinic directly."
_CONFIRMED_TEXT = "Thank you — your appointment is confirmed."
_HANDOFF_TEXT = (
    "Thanks — we've let the clinic know and someone will be in touch to arrange this."
)
_CANCEL_ON_PAGE_TEXT = (
    "Open this link on your phone or computer to cancel your appointment."
)
_TROUBLE_TEXT = (
    "Sorry — we couldn't complete that just now. Please contact the clinic directly."
)


def _reply(body: str, status_code: int = 200) -> PlainTextResponse:
    """Every response from this router carries the no-referrer headers."""
    return PlainTextResponse(
        body, status_code=status_code, headers=dict(LINK_RESPONSE_HEADERS)
    )


@router.get("/{action}", response_class=PlainTextResponse)
async def follow_campaign_link(
    action: str,
    request: Request,
    token: str = Query(..., description="Signed per-run action token"),
) -> PlainTextResponse:
    """Verify a campaign action token and carry out the action it authorises."""
    if action not in ACTIONS:
        return _reply(_INVALID_TEXT, 404)

    verified = verify_action_token(token)
    if verified == EXPIRED:
        return _reply(_EXPIRED_TEXT, 410)
    if verified == INVALID:
        return _reply(_INVALID_TEXT, 400)

    run_id, token_action = verified  # type: ignore[misc]
    if token_action != action:
        # The action is signed, so this means the path and token disagree.
        return _reply(_INVALID_TEXT, 400)

    async with get_system_db_session(
        "campaign_action_link", external_id=run_id
    ) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _reply(_GONE_TEXT, 410)

        institution = await session.get(Institution, run.institution_id)
        location = (
            await session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )

        _audit(action, run, request)

        if action == "confirm":
            body, code = await _confirm(session, run, institution, location)
        elif action == "cancel":
            # Cancelling is handled on the page behind an explicit confirmation,
            # never as a side effect of opening a link. Nothing happens here.
            body, code = _CANCEL_ON_PAGE_TEXT, 200
        else:
            body, code = await _hand_to_staff(session, run, action)

        await session.commit()

    return _reply(body, code)


async def _confirm(session, run, institution, location) -> tuple[str, int]:
    """Write the confirmation into the practice's software."""
    if institution is None or location is None or not run.trigger_ref_id:
        return _TROUBLE_TEXT, 500
    try:
        adapter = await get_adapter_for_institution_location(institution, location)
        result = await adapter.confirm_appointment(str(run.trigger_ref_id))
    except Exception:
        logger.exception("campaign link confirm failed: run=%s", run.id)
        return _TROUBLE_TEXT, 500

    if not getattr(result, "success", False):
        logger.warning("campaign link confirm rejected: run=%s", run.id)
        return _TROUBLE_TEXT, 502

    await _record_response(session, run, channel="booking_link", intent="confirmed")
    return _CONFIRMED_TEXT, 200


async def _hand_to_staff(session, run, action: str) -> tuple[str, int]:
    """Capture the patient's intent for an action we cannot complete unattended.

    Booking and rescheduling need the patient to choose a slot, and there is no
    patient-facing page for that yet. Recording the intent and raising a handoff
    is honest: the patient is told a person will follow up, and one actually will.
    """
    event = await _record_response(
        session, run, channel="booking_link", intent=f"requested_{action}"
    )
    session.add(
        CampaignStaffHandoff(
            id=str(uuid4()),
            institution_id=event.institution_id,
            location_id=event.location_id,
            workflow_id=event.workflow_id,
            workflow_run_id=event.workflow_run_id,
            contact_id=event.contact_id,
            response_event_id=str(event.id),
            reason=f"patient_requested_{action}",
            status="open",
            summary=f"Patient followed the {action} link from a campaign message.",
        )
    )
    return _HANDOFF_TEXT, 200


async def _record_response(session, run, *, channel: str, intent: str):
    """Record that the patient acted.

    This also stops the rest of the run's outreach on every channel (Item 16) —
    a patient who confirms from a text must not then be phoned about it.
    """
    event = CampaignResponseEvent(
        id=str(uuid4()),
        institution_id=str(run.institution_id),
        location_id=str(run.location_id) if run.location_id else None,
        workflow_id=str(run.workflow_id) if run.workflow_id else None,
        workflow_run_id=str(run.id),
        contact_id=str(run.contact_id) if run.contact_id else None,
        channel=channel,
        normalized_intent=intent,
        source="campaign_action_link",
        source_event_id=f"link:{run.id}:{intent}",
        confidence="deterministic",
    )
    session.add(event)
    await session.flush()
    return event


def _audit(action: str, run, request: Request) -> None:
    """Record the action against the run, never against a named patient."""
    log_audit_background(
        action=(
            AuditAction.CONFIRM_APPOINTMENT
            if action == "confirm"
            else AuditAction.UPDATE_APPOINTMENT
        ),
        actor=AuditActor.API_CLIENT,
        target_resource=f"campaign_run:{run.id}:link:{action}",
        institution_id=str(run.institution_id),
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "source": "campaign_action_link",
            "ip_address": get_client_ip(
                forwarded_for=request.headers.get("x-forwarded-for"),
                direct_host=request.client.host if request.client else None,
            ),
        },
    )
