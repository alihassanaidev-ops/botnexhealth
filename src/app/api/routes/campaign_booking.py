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
from datetime import date, datetime
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.app.database import get_campaign_link_db_session
from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
from src.app.models.automation_workflow import AutomationWorkflowRun
from src.app.services.automation.campaign_action_links import (
    BOOKING_LINK_CONFIG_KEY,
)
from src.app.services.automation.campaign_identity import is_verified
from src.app.models.campaign_response import CampaignResponseEvent, CampaignStaffHandoff
from src.app.models.contact import Contact
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.pms.factory import get_adapter_for_institution_location
from src.app.pms.models import BookingRequest, BookingWriteStatus
from src.app.services.write_provenance import WriteProvenance
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

#: Default window. A week is enough to find something without asking the
#: practice software for a month of availability on every page load.
DEFAULT_DAYS = 7
#: Ceiling on what a caller can ask for, so the window cannot be widened into a
#: denial-of-service against the clinic's practice software.
MAX_DAYS = 30

#: Actions that may pick a slot. Confirming needs no slot.
BOOKABLE_ACTIONS = ("book", "reschedule")

# A three-week search on a 15-minute grid returns thousands of slots — the
# sandbox returns over two thousand. Sending all of them is a quarter-megabyte
# payload and a page of a thousand buttons, which is the abandonment this flow
# is meant to avoid. Offer a choice, not a calendar dump.
#: Days with availability to offer at once.
MAX_DAYS_OFFERED = 4
#: Times per day, spread across that day rather than the first few in a row.
MAX_TIMES_PER_DAY = 8


def _spread(slots: list, limit: int) -> list:
    """Take up to ``limit`` slots spread evenly across the day.

    Taking the first N would offer 6:45, 7:00, 7:15 … — all before breakfast,
    and a patient who cannot do mornings sees nothing they can use. Sampling
    across the range shows the shape of the day instead.
    """
    if len(slots) <= limit:
        return slots
    step = (len(slots) - 1) / (limit - 1)
    return [slots[round(i * step)] for i in range(limit)]


def _offerable(slots: list) -> list:
    """Reduce a full availability search to something a person can scan."""
    by_day: dict[str, list] = {}
    for slot in slots:
        # The date portion of the ISO string is the clinic's local day, which is
        # the day the patient will read on the page.
        by_day.setdefault(str(slot.start)[:10], []).append(slot)

    offered: list = []
    for day in sorted(by_day)[:MAX_DAYS_OFFERED]:
        ordered = sorted(by_day[day], key=lambda s: s.start)
        offered.extend(_spread(ordered, MAX_TIMES_PER_DAY))
    return offered


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
    #: The two things taken from the client, and only these. The type decides
    #: how long the appointment needs to be, so the re-check has to search with
    #: it or a 45-minute root canal gets matched against a 30-minute opening.
    slot_start: str
    appointment_type_id: str | None = None


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


#: What a completed action is recorded as, per action.
COMPLETED_INTENT = {
    "book": "booked",
    "reschedule": "rescheduled",
    "cancel": "cancelled",
}


def _link_config(run: AutomationWorkflowRun) -> dict:
    """The rules a ``booking_link`` node recorded for this run.

    A run enrolled before the node existed, or a campaign that never used one,
    has no entry — which reads as "no restriction" so existing campaigns keep
    behaving exactly as they did.
    """
    metadata = run.trigger_metadata or {}
    config = metadata.get(BOOKING_LINK_CONFIG_KEY)
    return config if isinstance(config, dict) else {}


def _allowed_type_ids(run: AutomationWorkflowRun) -> set[str]:
    raw = _link_config(run).get("appointment_type_ids") or []
    return {str(t) for t in raw if str(t)}


def _configured_provider_id(run: AutomationWorkflowRun) -> str | None:
    raw = _link_config(run).get("provider_id")
    if raw is None:
        return None
    provider_id = str(raw).strip()
    return provider_id or None


def _type_is_allowed(
    run: AutomationWorkflowRun, appointment_type_id: str | None
) -> bool:
    """Whether this run may book that type.

    Enforced on the server for the reason the restriction exists at all: the
    voice agent's equivalent rule lives in its Retell prompt, so it is guidance
    rather than a constraint. A link that only *displayed* a filtered list would
    still book anything a crafted request asked for.
    """
    allowed = _allowed_type_ids(run)
    if not allowed:
        return True
    return appointment_type_id is not None and str(appointment_type_id) in allowed


def _window_days(run: AutomationWorkflowRun, default: int) -> int:
    value = _link_config(run).get("window_days")
    return value if isinstance(value, int) and value > 0 else default


#: Actions that disclose an existing appointment or destroy one. Booking is not
#: among them: it shows only the clinic's own free slots and can be undone.
SENSITIVE_ACTIONS = frozenset({"reschedule", "cancel"})


def _identity_required(run: AutomationWorkflowRun, action: str) -> bool:
    """Whether this run must prove identity before the action is allowed.

    Set by the booking_link step, so the campaign author decides. Defaults to
    guarding the sensitive actions when a run carries no config at all, which
    is the safer reading for a run enrolled before the setting existed.
    """
    config = _link_config(run)
    if not config:
        # No booking_link step ran at all, so no author ever expressed a view.
        # Runs already in flight when this shipped are in this state, and
        # turning their live links into a refusal they cannot clear is a worse
        # failure than the one the gate prevents. New campaigns get the safe
        # default because the node sets it explicitly.
        return False
    mode = config.get("identity_check") or "sensitive"
    if mode == "off":
        return False
    if mode == "always":
        return True
    return action in SENSITIVE_ACTIONS


def _action_permitted(run: AutomationWorkflowRun, action: str) -> bool:
    """Whether the node offered this action at all.

    The token already binds one action, so this is a second, independent check:
    a token issued for a run whose node only offers ``confirm`` must not book,
    even though the signature is valid.
    """
    actions = _link_config(run).get("actions")
    if not isinstance(actions, list) or not actions:
        return True
    return action in {str(a) for a in actions}


async def _already_booked(session, run_id: str, action: str = "book") -> bool:
    """Has this run already completed this action?

    A patient who books, then reopens the link from the same message, must not
    get a second appointment — and one who reschedules must not move the
    appointment twice.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(CampaignResponseEvent.id)
        .where(
            CampaignResponseEvent.workflow_run_id == run_id,
            CampaignResponseEvent.normalized_intent
            == COMPLETED_INTENT.get(action, "booked"),
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


def _duration_minutes(start: str, end: str | None) -> int | None:
    """Minutes between two ISO timestamps, or None when it cannot be worked out."""
    if not end:
        return None
    try:
        started = datetime.fromisoformat(start)
        finished = datetime.fromisoformat(end)
    except ValueError:
        return None
    minutes = int((finished - started).total_seconds() // 60)
    return minutes if minutes > 0 else None


async def _provider_names(adapter) -> dict[str, str]:
    """Map provider id to display name.

    The slot search returns provider ids but no names, and "who am I seeing?"
    is a fair question when a patient is choosing a time.
    """
    try:
        providers = await adapter.list_providers()
    except Exception:
        logger.warning("could not load provider names", exc_info=True)
        return {}
    return {p.id: (getattr(p, "name", "") or "") for p in providers}


async def _search_slots(
    institution,
    location,
    run,
    *,
    appointment_type_id: str | None = None,
    start_date: str | None = None,
    days: int | None = None,
    with_provider_names: bool = True,
) -> list:
    """Live availability for this run's clinic.

    The appointment type matters to the practice software, not just to the
    label: it decides how long the slot needs to be, so a 60-minute new-patient
    exam and a 30-minute filling do not offer the same times. This is the same
    argument the voice agent passes when it searches.
    """
    adapter = await get_adapter_for_institution_location(institution, location)
    metadata = run.trigger_metadata or {}
    # Names cost an extra call to the practice software and are only worth it
    # where they are shown. The pre-booking re-check displays nothing.
    names = await _provider_names(adapter) if with_provider_names else {}
    result = await adapter.find_available_slots(
        start_date=start_date or date.today().isoformat(),
        days=days or DEFAULT_DAYS,
        provider_id=_configured_provider_id(run),
        appointment_type_id=appointment_type_id or metadata.get("appointment_type_id"),
    )
    slots = list(getattr(result, "slots", []) or [])
    for slot in slots:
        if not getattr(slot, "provider_name", ""):
            slot.provider_name = names.get(slot.provider_id, "")
    return slots


async def _describe_appointment(session, run) -> dict | None:
    """What the patient is being asked to give up.

    Prefers the appointment projection, which reflects the practice software
    and stays right if the visit was moved after it was booked. Falls back to
    what was recorded at booking time, because that projection is populated by
    a sync that will not have run for an appointment made seconds ago.
    """
    from sqlalchemy import select

    from src.app.models.appointment_working_set import AppointmentWorkingSet

    raw_id = str(run.trigger_ref_id or "")
    bare_id = raw_id.split("-", 1)[1] if raw_id.startswith("nh-") else raw_id

    try:
        row = (
            await session.execute(
                select(AppointmentWorkingSet)
                .where(
                    AppointmentWorkingSet.institution_id == str(run.institution_id),
                    AppointmentWorkingSet.nexhealth_appointment_id.in_(
                        [raw_id, bare_id]
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    except Exception:
        logger.warning("appointment lookup failed for run=%s", run.id, exc_info=True)
        row = None

    if row is not None and row.start_time is not None:
        return {
            "start": row.start_time.isoformat(),
            "provider_name": "",
            "reason": row.appointment_reason or "",
        }

    metadata = run.trigger_metadata or {}
    if metadata.get("booked_start"):
        return {
            "start": metadata["booked_start"],
            "provider_name": metadata.get("booked_provider_name", ""),
            "reason": "",
        }
    return None


@router.get("/cancel/appointment")
async def cancellation_details(
    token: str = Query(..., description="Signed per-run action token"),
) -> JSONResponse:
    """What the patient is about to cancel.

    A read, never a write. Messaging apps and mail clients follow links to build
    previews, so a GET that cancelled would cancel appointments nobody tapped.
    The cancellation itself is the POST below.
    """
    verified = verify_action_token(token)
    if verified == EXPIRED:
        return _json({"error": "expired"}, 410)
    if verified == INVALID:
        return _json({"error": "invalid"}, 400)
    run_id, token_action = verified  # type: ignore[misc]
    if token_action != "cancel":
        return _json({"error": "invalid"}, 400)

    async with get_campaign_link_db_session(run_id) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)
        if await _already_booked(session, run_id, "cancel"):
            return _json({"already_cancelled": True})
        if not run.trigger_ref_id:
            return _json({"error": "no_appointment"}, 409)
        location = (
            await session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        if _identity_required(run, "cancel") and not is_verified(run):
            # The time, provider and reason of someone's appointment are not
            # shown to whoever happens to hold the link. The page sends them to
            # the identity step and comes back.
            return _json(
                {
                    "already_cancelled": False,
                    "clinic_name": getattr(location, "name", "") or "",
                    "appointment": None,
                    "identity_required": True,
                }
            )

        appointment = await _describe_appointment(session, run)
        return _json(
            {
                "already_cancelled": False,
                "clinic_name": getattr(location, "name", "") or "",
                "appointment": appointment,
                "identity_required": False,
            }
        )


@router.post("/cancel/appointment")
async def cancel_appointment_link(
    request: Request,
    token: str = Query(..., description="Signed per-run action token"),
) -> JSONResponse:
    """Cancel the appointment this run is about.

    Deliberately a POST behind an explicit confirmation on the page. Cancelling
    writes into a live schedule and cannot be undone by the patient, so it is
    not something a link preview, a prefetch or a mistaken tap should be able to
    do on its own.
    """
    verified = verify_action_token(token)
    if verified == EXPIRED:
        return _json({"error": "expired"}, 410)
    if verified == INVALID:
        return _json({"error": "invalid"}, 400)
    run_id, token_action = verified  # type: ignore[misc]
    if token_action != "cancel":
        return _json({"error": "invalid"}, 400)

    async with get_campaign_link_db_session(run_id) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)
        if _identity_required(run, "cancel") and not is_verified(run):
            # The destructive half of the two-step. Guarded independently of the
            # GET above: refusing to *describe* the appointment would be hollow
            # if the POST still cancelled it.
            return _json({"error": "identity_required"}, 403)
        if await _already_booked(session, run_id, "cancel"):
            # Tapping twice must not try to cancel an already-cancelled visit.
            return _json({"status": "already_cancelled"})
        if not run.trigger_ref_id:
            return _json({"error": "no_appointment"}, 409)

        institution = await session.get(Institution, run.institution_id)
        location = (
            await session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        if institution is None or location is None:
            return _json({"error": "unavailable"}, 503)

        try:
            adapter = await get_adapter_for_institution_location(institution, location)
            result = await adapter.cancel_appointment(str(run.trigger_ref_id))
        except Exception:
            logger.exception("cancel failed for run=%s", run_id)
            return _json({"error": "unavailable"}, 503)

        if not getattr(result, "success", False):
            return _json({"error": "could_not_cancel"}, 502)

        session.add(
            CampaignResponseEvent(
                id=str(uuid4()),
                institution_id=str(run.institution_id),
                location_id=str(run.location_id) if run.location_id else None,
                workflow_id=str(run.workflow_id) if run.workflow_id else None,
                workflow_run_id=str(run.id),
                contact_id=str(run.contact_id) if run.contact_id else None,
                channel="booking_link",
                normalized_intent="cancelled",
                source="campaign_booking_link",
                source_event_id=f"link:{run.id}:cancelled",
                confidence="deterministic",
            )
        )
        log_audit_background(
            action=AuditAction.CANCEL_APPOINTMENT,
            actor=AuditActor.API_CLIENT,
            target_resource=f"campaign_run:{run.id}:link:cancel",
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

    return _json({"status": "cancelled"})


class AppointmentTypeOption(BaseModel):
    id: str
    name: str
    duration_minutes: int | None = None


@router.get("/{action}/appointment-types")
async def list_appointment_types(
    action: str,
    token: str = Query(..., description="Signed per-run action token"),
) -> JSONResponse:
    """What the patient can book. Names the clinic already publishes."""
    verified = _verify(action, token)
    if isinstance(verified, JSONResponse):
        return verified
    run_id, _ = verified

    async with get_campaign_link_db_session(run_id) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)
        institution = await session.get(Institution, run.institution_id)
        location = (
            await session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        if institution is None or location is None:
            return _json({"error": "unavailable"}, 503)
        try:
            adapter = await get_adapter_for_institution_location(institution, location)
            types = await adapter.list_appointment_types()
        except Exception:
            logger.exception("appointment types failed for run=%s", run_id)
            return _json({"error": "unavailable"}, 503)

    allowed = _allowed_type_ids(run)
    return _json(
        {
            "appointment_types": [
                AppointmentTypeOption(
                    id=t.source_id or t.id,
                    name=t.name,
                    duration_minutes=getattr(t, "duration_minutes", None),
                ).model_dump()
                for t in types
                if not allowed or str(t.source_id or t.id) in allowed
            ]
        }
    )


@router.get("/{action}/slots")
async def list_slots(
    action: str,
    token: str = Query(..., description="Signed per-run action token"),
    appointment_type_id: str | None = Query(None),
    start_date: str | None = Query(None, description="ISO date to search from"),
    days: int = Query(DEFAULT_DAYS, ge=1, le=MAX_DAYS),
) -> JSONResponse:
    """Offer what the clinic actually has free, for this run's patient."""
    verified = _verify(action, token)
    if isinstance(verified, JSONResponse):
        return verified
    run_id, _ = verified

    async with get_campaign_link_db_session(run_id) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)

        if await _already_booked(session, run_id, action):
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
            slots = await _search_slots(
                institution,
                location,
                run,
                appointment_type_id=appointment_type_id,
                start_date=start_date,
                # An explicit ?days= still wins so the pre-booking re-check can
                # narrow the search; the node's window is the default when the
                # patient's page does not ask for one.
                days=days or _window_days(run, DEFAULT_DAYS),
            )
        except Exception:
            logger.exception("slot search failed for run=%s", run_id)
            return _json({"error": "unavailable"}, 503)

        return _json(
            SlotsResponse(
                slots=_as_options(_offerable(slots)),
                clinic_name=getattr(location, "name", "") or "",
                timezone=getattr(location, "timezone", None),
            ).model_dump()
        )


async def _send_booking_confirmation_email(
    *,
    session,
    institution_id: str,
    location,
    contact,
    adapter,
    run_id: str,
    appointment_start: str,
    provider_name: str,
    action: str,
) -> None:
    """Email the patient what they just booked. Best effort, never blocking.

    Deliberately mirrors the voice agent's post-call confirmation rather than
    inventing a second one: same template type, so a clinic edits its wording in
    one place and both channels follow it, and the same gate on the clinic having
    activated that template.

    Two things differ from the call path, both improvements that come from this
    being a link rather than a transcript. The time and provider are the values
    the practice software actually wrote, not a phrase the agent heard
    ("March 28th around 2:30pm"). And the address still comes from the PMS
    record rather than anything typed into the page, so a booking made from a
    forwarded link cannot redirect a patient's confirmation elsewhere.
    """
    from src.app.models.email_template import EmailTemplateType
    from src.app.services.email_notification_service import EmailNotificationService
    from src.app.services.email_template_service import EmailTemplateService

    template_type = EmailTemplateType.PATIENT_APPOINTMENT_CONFIRMATION.value
    template = await EmailTemplateService(session).get_template_by_type(
        institution_id, template_type
    )
    if not template or not template.is_active:
        return

    pms_patient_id = getattr(contact, "nexhealth_patient_id", None)
    if not pms_patient_id:
        return
    patient = await adapter.get_patient(str(pms_patient_id))
    patient_email = (getattr(patient, "email", "") or "").strip()
    if not patient_email:
        return

    pms_name = " ".join(
        p
        for p in (getattr(patient, "first_name", ""), getattr(patient, "last_name", ""))
        if p
    ).strip()
    await EmailNotificationService().send_notification(
        recipients=[patient_email],
        payload={
            "location_name": getattr(location, "name", "") or "",
            "appointment_patient_name": pms_name or "there",
            "appointment_datetime": appointment_start,
            "appointment_provider": provider_name,
            "appointment_service": "",
        },
        # Scoped to the run and the action, so reopening the link cannot send a
        # second confirmation but a later reschedule still sends its own.
        idempotency_key=f"campaign-link:{run_id}:{action}",
        template_type=template_type,
        institution_id=institution_id,
        patient_facing=True,
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

    async with get_campaign_link_db_session(run_id) as session:
        run = await session.get(AutomationWorkflowRun, run_id)
        if run is None:
            return _json({"error": "gone"}, 410)

        if _identity_required(run, action) and not is_verified(run):
            return _json({"error": "identity_required"}, 403)

        if not _action_permitted(run, action):
            # A valid signature for an action the campaign step never offered.
            return _json({"error": "action_not_offered"}, 403)

        if not _type_is_allowed(run, body.appointment_type_id):
            # The offered list is filtered, but a filtered list is only a
            # display. Refuse here so the restriction is a property of the
            # system rather than of the page the patient happened to load.
            return _json({"error": "appointment_type_not_offered"}, 403)

        if await _already_booked(session, run_id, action):
            # Reopening the link from the same message must not act twice.
            return _json({"status": "already_booked"})

        institution = await session.get(Institution, run.institution_id)
        location = (
            await session.get(InstitutionLocation, run.location_id)
            if run.location_id
            else None
        )
        contact = await session.get(Contact, run.contact_id) if run.contact_id else None
        if institution is None or location is None or contact is None:
            return _json({"error": "unavailable"}, 503)

        patient_id = contact.nexhealth_patient_id
        if not patient_id:
            # Nothing in the practice software to book against — an enquiry who
            # was never registered, or a record that has not synced. Returning
            # an error and stopping would drop the patient silently: they wanted
            # an appointment and nobody at the clinic would ever know. Record
            # the intent and raise a handoff, the same as the landing page does
            # for an action it cannot complete unattended.
            event = CampaignResponseEvent(
                id=str(uuid4()),
                institution_id=str(run.institution_id),
                location_id=str(run.location_id) if run.location_id else None,
                workflow_id=str(run.workflow_id) if run.workflow_id else None,
                workflow_run_id=str(run.id),
                contact_id=str(run.contact_id) if run.contact_id else None,
                channel="booking_link",
                normalized_intent="requested_book",
                source="campaign_booking_link",
                source_event_id=f"link:{run.id}:requested_book",
                confidence="deterministic",
            )
            session.add(event)
            await session.flush()
            session.add(
                CampaignStaffHandoff(
                    id=str(uuid4()),
                    institution_id=event.institution_id,
                    location_id=event.location_id,
                    workflow_id=event.workflow_id,
                    workflow_run_id=event.workflow_run_id,
                    contact_id=event.contact_id,
                    response_event_id=str(event.id),
                    reason="patient_not_in_practice_software",
                    status="open",
                    summary=(
                        "Patient chose a time from a booking link but has no "
                        "record in the practice software."
                    ),
                )
            )
            await session.commit()
            return _json({"error": "handoff"}, 409)

        # Re-search and match. The client's start time selects a slot; it never
        # supplies one. A slot taken while the patient was deciding simply is
        # not in this list any more.
        try:
            slots = await _search_slots(
                institution,
                location,
                run,
                appointment_type_id=body.appointment_type_id,
                with_provider_names=False,
            )
        except Exception:
            logger.exception("slot re-check failed for run=%s", run_id)
            return _json({"error": "unavailable"}, 503)

        chosen = next((s for s in slots if s.start == body.slot_start), None)
        if chosen is None:
            # Carry the current list back with the refusal. The page can re-offer
            # immediately instead of making the patient wait through a second
            # request, and load time is what loses them.
            return _json(
                {"error": "slot_taken", "slots": _as_options(_offerable(slots))}, 409
            )

        booking = BookingRequest(
            patient_id=str(patient_id),
            provider_id=chosen.provider_id,
            slot_start=chosen.start,
            slot_end=chosen.end,
            # GoTracker reschedules by updating the appointment and takes the
            # length as minutes, not as an end time. Without this a rescheduled
            # visit can come back with no duration at all.
            duration_min=_duration_minutes(chosen.start, chosen.end),
            operatory_id=getattr(chosen, "operatory_id", None),
            # The slot's own type when the practice software supplies one,
            # otherwise what the patient chose — never neither, or the
            # appointment is booked at the wrong length.
            appointment_type_id=(
                getattr(chosen, "appointment_type_id", None) or body.appointment_type_id
            ),
            # Item 34. Recorded as PATIENT_LINK rather than CAMPAIGN even though
            # a run id is present: the campaign sent the link, the patient chose
            # the slot, and an investigation into an unexpected booking needs to
            # know which of those to look at.
            provenance=WriteProvenance.for_patient_link(
                workflow_run_id=str(run.id)
            ).as_payload(),
        )

        try:
            adapter = await get_adapter_for_institution_location(institution, location)
            if action == "reschedule":
                # Rescheduling moves the existing appointment; it does not book
                # a second one. Going through book_appointment would leave the
                # original standing and the patient holding two slots.
                #
                # reschedule_appointment_v2 patches the appointment directly
                # where the practice software supports it, and falls back to
                # book-then-cancel where it does not, so the adapter decides the
                # strategy rather than this route guessing at it.
                if not run.trigger_ref_id:
                    logger.warning("reschedule with no appointment on run=%s", run_id)
                    return _json({"error": "handoff"}, 409)
                result = await adapter.reschedule_appointment_v2(
                    str(run.trigger_ref_id), booking
                )
            else:
                result = await adapter.book_appointment(booking)
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
                fresh = await _search_slots(
                    institution,
                    location,
                    run,
                    appointment_type_id=body.appointment_type_id,
                )
                taken = not any(s.start == body.slot_start for s in fresh)
            except Exception:
                logger.exception("post-failure slot re-check failed run=%s", run_id)
                taken = False

            if taken:
                return _json(
                    {"error": "slot_taken", "slots": _as_options(_offerable(fresh))},
                    409,
                )
            return _json({"error": "could_not_book"}, 502)

        pending = (
            getattr(result, "write_status", "") == BookingWriteStatus.PENDING.value
        )

        # Record which appointment this run is now about. Confirm, reschedule
        # and cancel all read this, so without it a patient who booked through
        # the link could not afterwards change or cancel what they booked.
        if getattr(result, "id", None):
            run.trigger_ref_id = str(result.id)
        # Keep enough to describe the appointment back to the patient. The
        # appointment projection is the better source, but it is populated by a
        # sync that has not necessarily run yet for something booked seconds ago.
        run.trigger_metadata = {
            **(run.trigger_metadata or {}),
            "booked_start": chosen.start,
            "booked_end": chosen.end,
            "booked_provider_name": getattr(chosen, "provider_name", "") or "",
        }
        if not pending and contact.lead_status is not None:
            from src.app.models.campaign_enquiry import EnquiryStatus

            contact.lead_status = EnquiryStatus.BOOKED.value

        session.add(
            CampaignResponseEvent(
                id=str(uuid4()),
                institution_id=str(run.institution_id),
                location_id=str(run.location_id) if run.location_id else None,
                workflow_id=str(run.workflow_id) if run.workflow_id else None,
                workflow_run_id=str(run.id),
                contact_id=str(run.contact_id) if run.contact_id else None,
                channel="booking_link",
                normalized_intent=COMPLETED_INTENT.get(action, "booked"),
                source="campaign_booking_link",
                source_event_id=f"link:{run.id}:{COMPLETED_INTENT.get(action, 'booked')}",
                confidence="deterministic",
            )
        )
        log_audit_background(
            action=(
                AuditAction.RESCHEDULE_APPOINTMENT
                if action == "reschedule"
                else AuditAction.BOOK_APPOINTMENT
            ),
            actor=AuditActor.API_CLIENT,
            target_resource=f"campaign_run:{run.id}:link:{action}",
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

        # After the commit, and never allowed to undo it. A booking that
        # succeeded must stay booked even if the mail provider is down; the
        # patient still sees the confirmation on the page. Pending bookings send
        # nothing — there is nothing confirmed to confirm yet.
        if not pending:
            try:
                await _send_booking_confirmation_email(
                    session=session,
                    institution_id=str(run.institution_id),
                    location=location,
                    contact=contact,
                    adapter=adapter,
                    run_id=str(run.id),
                    appointment_start=chosen.start,
                    provider_name=getattr(chosen, "provider_name", "") or "",
                    action=action,
                )
            except Exception:
                logger.exception(
                    "confirmation email failed after booking run=%s", run_id
                )

    # "pending" is the GoTracker case: accepted, not yet in the practice
    # software. The page must not tell the patient it is confirmed.
    return _json({"status": "pending" if pending else "booked", "start": chosen.start})
