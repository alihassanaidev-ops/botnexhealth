"""The slot-picking API behind a campaign booking link (Item 12).

A patient opens this from a text message with no login, so the signed token is
the whole of the authentication and the same three rules from
``campaign_links`` apply: say nothing they do not already know, send
``no-referrer`` on every response, and tell an expired link apart from a forged
one.

Two endpoints, deliberately only two: research on patient self-scheduling is
consistent that tap count and load time are what drive abandonment, so the page
lists slots and books one. Nothing else.

**The client is never trusted with the booking parameters.** It sends back only
the start time it chose; the server re-runs the availability search and books
from the slot it found itself. Otherwise a crafted request could book any time,
any provider, any length — and the slot may in any case have gone while the
patient was deciding, which is the same re-check the messaging steps already do
before every send.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.app.database import get_system_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.models.campaign_response import CampaignResponseEvent
from src.app.models.contact import Contact
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.pms.factory import get_adapter_for_institution_location
from src.app.pms.models import BookingRequest, BookingWriteStatus
from src.app.security import get_client_ip
from src.app.services.audit import log_audit_background
from src.app.services.automation.campaign_action_links import (
    EXPIRED,
    INVALID,
    LINK_RESPONSE_HEADERS,
    verify_action_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns/link", tags=["Campaign Links"])

#: How far ahead to offer. Long enough to find something, short enough that the
#: practice software is not asked for a huge range on every page load.
SEARCH_DAYS = 21

#: Actions that may pick a slot. Confirming needs no slot.
BOOKABLE_ACTIONS = ("book", "reschedule")


class SlotOption(BaseModel):
    """One offerable slot. Carries nothing about the patient."""

    start: str
    end: str
    provider_id: str
    provider_name: str = ""


class SlotsResponse(BaseModel):
    slots: list[SlotOption] = Field(default_factory=list)
    clinic_name: str = ""
    timezone: str | None = None
    #: Set when the run has already been booked, so the page can say so rather
    #: than offering to book a second time.
    already_booked: bool = False


class BookRequest(BaseModel):
    #: The only thing taken from the client. Everything else is re-derived.
    slot_start: str


def _json(payload: dict, status_code: int = 200) -> JSONResponse:
    """Every response carries the same headers as the landing pages."""
    return JSONResponse(
        payload, status_code=status_code, headers=dict(LINK_RESPONSE_HEADERS)
    )


def _verify(action: str, token: str) -> tuple[str, str] | JSONResponse:
    """Shared token check. Returns the run id, or a response to send back."""
    if action not in BOOKABLE_ACTIONS:
        return _json({"error": "unknown_action"}, 404)
    verified = verify_action_token(token)
    if verified == EXPIRED:
        return _json({"error": "expired"}, 410)
    if verified == INVALID:
        return _json({"error": "invalid"}, 400)
    run_id, token_action = verified  # type: ignore[misc]
    if token_action != action:
        return _json({"error": "invalid"}, 400)
    return run_id, token_action


async def _already_booked(session, run_id: str) -> bool:
    """Has this run already produced a booking?

    A patient who books, then reopens the link from the same message, must not
    book a second appointment.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(CampaignResponseEvent.id)
        .where(
            CampaignResponseEvent.workflow_run_id == run_id,
            CampaignResponseEvent.normalized_intent == "booked",
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _as_options(slots) -> list[dict]:
    """Serialise slots for the wire. Nothing here identifies the patient."""
    return [
        SlotOption(
            start=s.start,
            end=s.end,
            provider_id=s.provider_id,
            provider_name=getattr(s, "provider_name", "") or "",
        ).model_dump()
        for s in slots
    ]


async def _search_slots(institution, location, run) -> list:
    """Live availability for this run's clinic."""
    adapter = await get_adapter_for_institution_location(institution, location)
    metadata = run.trigger_metadata or {}
    result = await adapter.find_available_slots(
        start_date=date.today().isoformat(),
        days=SEARCH_DAYS,
        appointment_type_id=metadata.get("appointment_type_id"),
    )
    return list(getattr(result, "slots", []) or [])


@router.get("/{action}/slots")
async def list_slots(
    action: str,
    token: str = Query(..., description="Signed per-run action token"),
) -> JSONResponse:
    """Offer what the clinic actually has free, for this run's patient."""
    verified = _verify(action, token)
    if isinstance(verified, JSONResponse):
        return verified
    run_id, _ = verified

    async with get_system_db_session(
        "campaign_booking_link", external_id=run_id
    ) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)

        if await _already_booked(session, run_id):
            return _json(SlotsResponse(already_booked=True).model_dump())

        institution = await session.get(Institution, run.institution_id)
        location = (
            await session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        if institution is None or location is None:
            return _json({"error": "unavailable"}, 503)

        try:
            slots = await _search_slots(institution, location, run)
        except Exception:
            logger.exception("slot search failed for run=%s", run_id)
            return _json({"error": "unavailable"}, 503)

        return _json(
            SlotsResponse(
                slots=_as_options(slots),
                clinic_name=getattr(location, "name", "") or "",
                timezone=getattr(location, "timezone", None),
            ).model_dump()
        )


@router.post("/{action}/slots")
async def book_slot(
    action: str,
    body: BookRequest,
    request: Request,
    token: str = Query(..., description="Signed per-run action token"),
) -> JSONResponse:
    """Book the slot the patient chose, after proving it is still free."""
    verified = _verify(action, token)
    if isinstance(verified, JSONResponse):
        return verified
    run_id, _ = verified

    async with get_system_db_session(
        "campaign_booking_link", external_id=run_id
    ) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)

        if await _already_booked(session, run_id):
            # Reopening the link from the same message must not book twice.
            return _json({"status": "already_booked"})

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

        patient_id = contact.nexhealth_patient_id
        if not patient_id:
            # No record in the practice software to book against.
            return _json({"error": "handoff"}, 409)

        # Re-search and match. The client's start time selects a slot; it never
        # supplies one. A slot taken while the patient was deciding simply is
        # not in this list any more.
        try:
            slots = await _search_slots(institution, location, run)
        except Exception:
            logger.exception("slot re-check failed for run=%s", run_id)
            return _json({"error": "unavailable"}, 503)

        chosen = next((s for s in slots if s.start == body.slot_start), None)
        if chosen is None:
            # Carry the current list back with the refusal. The page can re-offer
            # immediately instead of making the patient wait through a second
            # request, and load time is what loses them.
            return _json({"error": "slot_taken", "slots": _as_options(slots)}, 409)

        try:
            adapter = await get_adapter_for_institution_location(institution, location)
            result = await adapter.book_appointment(
                BookingRequest(
                    patient_id=str(patient_id),
                    provider_id=chosen.provider_id,
                    slot_start=chosen.start,
                    slot_end=chosen.end,
                    operatory_id=getattr(chosen, "operatory_id", None),
                    appointment_type_id=getattr(chosen, "appointment_type_id", None),
                )
            )
        except Exception:
            logger.exception("booking failed for run=%s", run_id)
            return _json({"error": "unavailable"}, 503)

        if not getattr(result, "success", False):
            # Distinguish "someone took it" from "something broke", because the
            # patient can act on the first and not the second. Both practice
            # systems reject a taken slot, but as a generic failure: NexHealth
            # re-validates immediately before writing, and the GoTracker cloud
            # service checks working hours and existing appointments in one
            # transaction so two bookings cannot collide.
            #
            # Rather than match on their error text, ask the question directly —
            # is the slot still on offer? Gone means it was taken between our
            # check and the write, which is a real race: the voice agent books
            # through this same path, so a phone call can fill the slot while
            # the patient is looking at it.
            try:
                fresh = await _search_slots(institution, location, run)
                taken = not any(s.start == body.slot_start for s in fresh)
            except Exception:
                logger.exception("post-failure slot re-check failed run=%s", run_id)
                taken = False

            if taken:
                return _json(
                    {"error": "slot_taken", "slots": _as_options(fresh)}, 409
                )
            return _json({"error": "could_not_book"}, 502)

        pending = (
            getattr(result, "write_status", "") == BookingWriteStatus.PENDING.value
        )

        session.add(
            CampaignResponseEvent(
                id=str(uuid4()),
                institution_id=str(run.institution_id),
                location_id=str(run.location_id) if run.location_id else None,
                workflow_id=str(run.workflow_id) if run.workflow_id else None,
                workflow_run_id=str(run.id),
                contact_id=str(run.contact_id) if run.contact_id else None,
                channel="booking_link",
                normalized_intent="booked",
                source="campaign_booking_link",
                source_event_id=f"link:{run.id}:booked",
                confidence="deterministic",
            )
        )
        log_audit_background(
            action=AuditAction.BOOK_APPOINTMENT,
            actor=AuditActor.API_CLIENT,
            target_resource=f"campaign_run:{run.id}:link:book",
            institution_id=str(run.institution_id),
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "source": "campaign_booking_link",
                "ip_address": get_client_ip(
                    forwarded_for=request.headers.get("x-forwarded-for"),
                    direct_host=request.client.host if request.client else None,
                ),
            },
        )
        await session.commit()

    # "pending" is the GoTracker case: accepted, not yet in the practice
    # software. The page must not tell the patient it is confirmed.
    return _json({"status": "pending" if pending else "booked", "start": chosen.start})
