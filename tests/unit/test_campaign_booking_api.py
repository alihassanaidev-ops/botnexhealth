"""Item 12 · the slot picker a patient uses from a booking link.

Public, token-only. The tests below cover what a stranger can do, what the
client is allowed to influence, and what happens when the same link is opened
twice.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.api.routes.campaign_booking import router
from src.app.pms.models import BookingWriteStatus
from src.app.services.automation.campaign_action_links import (
    BOOKING_LINK_CONFIG_KEY,
    make_action_token,
)
from src.app.services.automation.campaign_identity import VERIFIED_KEY


def client_():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def client():
    return client_()


def _expired(action: str = "book") -> str:
    return make_action_token(
        "run-1", action, ttl_seconds=1, now=int(time.time()) - 9999
    )


def _run():
    run = MagicMock()
    run.id = "run-1"
    run.institution_id = "inst-1"
    run.location_id = "loc-1"
    run.workflow_id = "wf-1"
    run.contact_id = "c-1"
    run.trigger_metadata = {}
    run.trigger_ref_id = "appt-77"
    return run


def _slot(start="2026-09-02T14:00:00Z", provider="prov-1"):
    s = MagicMock()
    s.start = start
    s.end = "2026-09-02T14:30:00Z"
    s.provider_id = provider
    s.provider_name = "Dr Smith"
    s.operatory_id = None
    s.appointment_type_id = None
    return s


def _ctx(
    *,
    run=None,
    contact_pms_id="pms-9",
    already_booked=False,
    slots=None,
    book_success=True,
    write_status=BookingWriteStatus.CONFIRMED.value,
    lead_status=None,
):
    """Patch the module's collaborators for one request."""
    session = AsyncMock()
    run = run if run is not None else _run()
    contact = MagicMock()
    contact.nexhealth_patient_id = contact_pms_id
    contact.lead_status = lead_status
    # MagicMock(name=...) sets the mock's own name, not a .name attribute.
    location = MagicMock(timezone="America/Toronto")
    location.name = "Downtown"

    def _get(model, pk):
        from src.app.models.automation_workflow import AutomationWorkflowRun
        from src.app.models.contact import Contact
        from src.app.models.institution_location import InstitutionLocation

        if model is AutomationWorkflowRun:
            return run
        if model is Contact:
            return contact
        if model is InstitutionLocation:
            return location
        return MagicMock()

    session.get = AsyncMock(side_effect=_get)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.contact = contact

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    adapter = AsyncMock()
    default = [_slot()] if slots is None else slots
    if isinstance(default, list) and default and isinstance(default[0], list):
        # a sequence of results, one per successive search
        adapter.find_available_slots = AsyncMock(
            side_effect=[MagicMock(slots=batch) for batch in default]
        )
    else:
        adapter.find_available_slots = AsyncMock(return_value=MagicMock(slots=default))
    adapter.book_appointment = AsyncMock(
        return_value=MagicMock(success=book_success, write_status=write_status)
    )
    adapter.reschedule_appointment_v2 = AsyncMock(
        return_value=MagicMock(success=book_success, write_status=write_status)
    )

    return session, _Ctx(), adapter, AsyncMock(return_value=already_booked)


def _call(client, method, path, ctx, json=None):
    session, cm, adapter, booked = ctx
    with (
        patch(
            "src.app.api.routes.campaign_booking.get_system_db_session", return_value=cm
        ),
        patch(
            "src.app.api.routes.campaign_booking.get_adapter_for_institution_location",
            AsyncMock(return_value=adapter),
        ),
        patch("src.app.api.routes.campaign_booking._already_booked", booked),
        patch("src.app.api.routes.campaign_booking.log_audit_background"),
    ):
        return client.request(method, path, json=json)


class TestTokenGate:
    def test_a_forged_token_gets_nothing(self, client):
        r = _call(
            client, "GET", "/api/campaigns/link/book/slots?token=a.book.9.bad", _ctx()
        )
        assert r.status_code == 400

    def test_an_expired_token_says_so(self, client):
        r = _call(
            client, "GET", f"/api/campaigns/link/book/slots?token={_expired()}", _ctx()
        )
        assert r.status_code == 410

    def test_confirm_cannot_reach_the_slot_picker(self, client):
        """Only book and reschedule pick slots; confirm needs no slot."""
        token = make_action_token("run-1", "confirm")
        r = _call(
            client, "GET", f"/api/campaigns/link/confirm/slots?token={token}", _ctx()
        )
        assert r.status_code == 404

    def test_a_book_token_cannot_be_replayed_on_reschedule(self, client):
        token = make_action_token("run-1", "book")
        r = _call(
            client, "GET", f"/api/campaigns/link/reschedule/slots?token={token}", _ctx()
        )
        assert r.status_code == 400

    def test_every_response_sends_no_referrer(self, client):
        r = _call(client, "GET", "/api/campaigns/link/book/slots?token=junk", _ctx())
        assert r.headers["referrer-policy"] == "no-referrer"


class TestListingSlots:
    def test_slots_are_offered_with_no_patient_detail(self, client):
        token = make_action_token("run-1", "book")
        r = _call(
            client, "GET", f"/api/campaigns/link/book/slots?token={token}", _ctx()
        )
        assert r.status_code == 200
        body = r.json()
        assert body["slots"][0]["start"] == "2026-09-02T14:00:00Z"
        # nothing identifying the patient, the run or the clinic's internals
        for leak in ("run-1", "c-1", "inst-1", "pms-9"):
            assert leak not in r.text

    def test_an_already_booked_run_is_not_offered_more_slots(self, client):
        token = make_action_token("run-1", "book")
        r = _call(
            client,
            "GET",
            f"/api/campaigns/link/book/slots?token={token}",
            _ctx(already_booked=True),
        )
        assert r.json()["already_booked"] is True
        assert r.json()["slots"] == []


class TestBooking:
    def test_booking_the_offered_slot_succeeds(self, client):
        token = make_action_token("run-1", "book")
        ctx = _ctx()
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "booked"
        _session, _cm, adapter, _b = ctx
        adapter.book_appointment.assert_awaited_once()

    def test_a_failing_confirmation_email_does_not_lose_the_booking(self, client):
        """The appointment is written before the email is attempted, and the
        email is explicitly not allowed to undo it. A mail outage must not make
        a patient who booked believe they did not."""
        token = make_action_token("run-1", "book")
        ctx = _ctx()
        with patch(
            "src.app.api.routes.campaign_booking._send_booking_confirmation_email",
            AsyncMock(side_effect=RuntimeError("smtp is down")),
        ):
            r = _call(
                client,
                "POST",
                f"/api/campaigns/link/book/slots?token={token}",
                ctx,
                json={"slot_start": "2026-09-02T14:00:00Z"},
            )
        assert r.status_code == 200
        assert r.json()["status"] == "booked"
        _session, _cm, adapter, _b = ctx
        adapter.book_appointment.assert_awaited_once()

    def test_a_pending_write_sends_no_confirmation(self, client):
        """Nothing is confirmed yet, so confirming it would be a lie."""
        token = make_action_token("run-1", "book")
        ctx = _ctx(write_status=BookingWriteStatus.PENDING.value)
        sender = AsyncMock()
        with patch(
            "src.app.api.routes.campaign_booking._send_booking_confirmation_email",
            sender,
        ):
            r = _call(
                client,
                "POST",
                f"/api/campaigns/link/book/slots?token={token}",
                ctx,
                json={"slot_start": "2026-09-02T14:00:00Z"},
            )
        assert r.json()["status"] == "pending"
        sender.assert_not_awaited()

    def test_confirmed_booking_marks_a_lead_booked(self, client):
        from src.app.models.campaign_enquiry import EnquiryStatus

        token = make_action_token("run-1", "book")
        ctx = _ctx(lead_status=EnquiryStatus.QUALIFIED.value)

        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )

        assert r.status_code == 200
        session, _cm, _adapter, _b = ctx
        assert session.contact.lead_status == EnquiryStatus.BOOKED.value

    def test_pending_booking_write_does_not_mark_a_lead_booked(self, client):
        from src.app.models.campaign_enquiry import EnquiryStatus

        token = make_action_token("run-1", "book")
        ctx = _ctx(
            write_status=BookingWriteStatus.PENDING.value,
            lead_status=EnquiryStatus.QUALIFIED.value,
        )

        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )

        assert r.status_code == 200
        session, _cm, _adapter, _b = ctx
        assert session.contact.lead_status == EnquiryStatus.QUALIFIED.value

    def test_the_client_cannot_choose_the_booking_parameters(self, client):
        """It sends a start time; the server books the slot it found itself."""
        token = make_action_token("run-1", "book")
        ctx = _ctx(slots=[_slot(provider="the-real-provider")])
        _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={
                "slot_start": "2026-09-02T14:00:00Z",
                "provider_id": "attacker-choice",
            },
        )
        _session, _cm, adapter, _b = ctx
        booked = adapter.book_appointment.await_args.args[0]
        assert booked.provider_id == "the-real-provider"

    def test_a_time_that_is_not_on_offer_is_refused(self, client):
        """Covers both a crafted request and a slot taken while deciding."""
        token = make_action_token("run-1", "book")
        ctx = _ctx()
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-12-25T03:00:00Z"},
        )
        assert r.status_code == 409
        assert r.json()["error"] == "slot_taken"
        _session, _cm, adapter, _b = ctx
        adapter.book_appointment.assert_not_awaited()

    def test_reopening_the_link_does_not_book_twice(self, client):
        token = make_action_token("run-1", "book")
        ctx = _ctx(already_booked=True)
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.json()["status"] == "already_booked"
        _session, _cm, adapter, _b = ctx
        adapter.book_appointment.assert_not_awaited()

    def test_a_gotracker_pending_write_is_not_reported_as_booked(self, client):
        """Item 4 again: accepted is not the same as in the practice software."""
        token = make_action_token("run-1", "book")
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            _ctx(write_status=BookingWriteStatus.PENDING.value),
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.json()["status"] == "pending"

    def test_a_rejected_booking_does_not_claim_success(self, client):
        token = make_action_token("run-1", "book")
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            _ctx(book_success=False),
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 502

    def test_a_patient_with_no_practice_record_is_handed_off(self, client):
        token = make_action_token("run-1", "book")
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            _ctx(contact_pms_id=None),
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 409
        assert r.json()["error"] == "handoff"


class TestLosingTheSlotToSomeoneElse:
    """The voice agent books through this same path, so a phone call can fill
    the slot while the patient is looking at it."""

    def test_a_slot_filled_between_the_check_and_the_write_re_offers(self, client):
        """The practice software rejects it, and the slot is gone on re-search.

        That is a race, not a fault: the patient can act on it, so they get a
        409 the page turns into "pick another" rather than a dead end.
        """
        token = make_action_token("run-1", "book")
        ctx = _ctx(
            book_success=False,
            # first search offers it, post-failure search no longer does
            slots=[[_slot()], []],
        )
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 409
        assert r.json()["error"] == "slot_taken"

    def test_a_genuine_failure_is_not_disguised_as_a_race(self, client):
        """Still on offer after the failure, so something actually broke."""
        token = make_action_token("run-1", "book")
        ctx = _ctx(book_success=False, slots=[[_slot()], [_slot()]])
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 502
        assert r.json()["error"] == "could_not_book"

    def test_a_failed_re_check_does_not_claim_the_slot_was_taken(self, client):
        """If we cannot tell, say the honest thing rather than guess."""
        token = make_action_token("run-1", "book")
        ctx = _ctx(book_success=False, slots=[[_slot()]])  # second search raises
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 502


class TestWhatIsActuallyOffered:
    """A three-week search on a 15-minute grid returns thousands of slots.

    The sandbox returns over two thousand — a quarter-megabyte payload and a
    page of a thousand buttons, on a phone, which is the abandonment this whole
    flow exists to avoid.
    """

    def test_a_huge_availability_is_reduced_to_something_scannable(self, client):
        from src.app.api.routes.campaign_booking import (
            MAX_DAYS_OFFERED,
            MAX_TIMES_PER_DAY,
        )

        many = [
            _slot(start=f"2026-09-{day:02d}T{hour:02d}:{minute:02d}:00Z")
            for day in range(1, 15)
            for hour in range(7, 18)
            for minute in (0, 15, 30, 45)
        ]
        token = make_action_token("run-1", "book")
        r = _call(
            client,
            "GET",
            f"/api/campaigns/link/book/slots?token={token}",
            _ctx(slots=many),
        )
        offered = r.json()["slots"]
        assert len(offered) <= MAX_DAYS_OFFERED * MAX_TIMES_PER_DAY
        assert len(offered) < len(many) / 10

    def test_times_are_spread_across_the_day_not_bunched_at_the_start(self):
        """Otherwise every option is before breakfast and a patient who cannot
        do mornings sees nothing they can use."""
        from src.app.api.routes.campaign_booking import _spread

        day = [_slot(start=f"2026-09-01T{h:02d}:00:00Z") for h in range(8, 18)]
        picked = _spread(day, 4)
        assert len(picked) == 4
        assert picked[0].start.endswith("08:00:00Z")
        assert picked[-1].start.endswith("17:00:00Z"), "must reach the end of the day"

    def test_booking_still_accepts_any_genuinely_free_slot(self, client):
        """Trimming is presentation. A slot we did not render is still bookable."""
        many = [_slot(start=f"2026-09-01T{h:02d}:00:00Z") for h in range(7, 18)]
        token = make_action_token("run-1", "book")
        ctx = _ctx(slots=many)
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-01T13:00:00Z"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "booked"


class TestRescheduleMovesRatherThanRebooks:
    """Rescheduling patches the existing appointment. Booking a second one and
    leaving the original standing would put the patient in two slots."""

    def test_reschedule_does_not_create_a_second_appointment(self, client):
        token = make_action_token("run-1", "reschedule")
        ctx = _ctx()
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/reschedule/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 200
        _s, _c, adapter, _b = ctx
        adapter.book_appointment.assert_not_awaited()
        adapter.reschedule_appointment_v2.assert_awaited_once()

    def test_it_moves_the_appointment_the_run_is_about(self, client):
        token = make_action_token("run-1", "reschedule")
        ctx = _ctx()
        _call(
            client,
            "POST",
            f"/api/campaigns/link/reschedule/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        _s, _c, adapter, _b = ctx
        old_id, booking = adapter.reschedule_appointment_v2.await_args.args
        assert old_id == "appt-77"
        assert booking.slot_start == "2026-09-02T14:00:00Z"

    def test_a_run_with_no_appointment_is_handed_to_staff(self, client):
        """Nothing to move — do not silently book a new one instead."""
        run = _run()
        run.trigger_ref_id = None
        token = make_action_token("run-1", "reschedule")
        ctx = _ctx(run=run)
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/reschedule/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 409
        assert r.json()["error"] == "handoff"
        _s, _c, adapter, _b = ctx
        adapter.book_appointment.assert_not_awaited()

    def test_booking_still_books(self, client):
        """The book action must not be diverted into a reschedule."""
        token = make_action_token("run-1", "book")
        ctx = _ctx()
        _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        _s, _c, adapter, _b = ctx
        adapter.book_appointment.assert_awaited_once()
        adapter.reschedule_appointment_v2.assert_not_awaited()


class TestWorksOnBothPracticeSystems:
    def test_duration_is_supplied_for_gotracker_updates(self, client):
        """GoTracker reschedules by updating the appointment and takes the
        length in minutes, not an end time. Without it a rescheduled visit can
        come back with no duration."""
        token = make_action_token("run-1", "book")
        ctx = _ctx()
        _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        _s, _c, adapter, _b = ctx
        booking = adapter.book_appointment.await_args.args[0]
        assert booking.duration_min == 30

    def test_a_queued_gotracker_write_is_reported_as_pending(self, client):
        """A GoTracker booking may sit in a queue for hours. The page must not
        tell the patient the practice has it."""
        token = make_action_token("run-1", "book")
        r = _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            _ctx(write_status=BookingWriteStatus.PENDING.value),
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.json()["status"] == "pending"

    def test_the_pre_booking_recheck_skips_provider_names(self, client):
        """Names cost an extra call to the practice software and nothing on the
        confirm path displays them."""
        token = make_action_token("run-1", "book")
        ctx = _ctx()
        _call(
            client,
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        _s, _c, adapter, _b = ctx
        adapter.list_providers.assert_not_awaited()

    def test_listing_slots_does_resolve_names(self, client):
        token = make_action_token("run-1", "book")
        ctx = _ctx()
        _call(client, "GET", f"/api/campaigns/link/book/slots?token={token}", ctx)
        _s, _c, adapter, _b = ctx
        adapter.list_providers.assert_awaited()


def _configured_run(identity_check="sensitive", verified=False, provider_id=None):
    """A run that a booking_link step actually configured."""
    run = _run()
    run.trigger_metadata = {
        BOOKING_LINK_CONFIG_KEY: {
            "actions": ["book", "reschedule", "cancel"],
            "appointment_type_ids": [],
            "window_days": 7,
            "provider_id": provider_id,
            "identity_check": identity_check,
            "node_id": "b1",
        }
    }
    if verified:
        run.trigger_metadata[VERIFIED_KEY] = True
    return run


class TestIdentityGate:
    """Who may act, once a booking_link step has expressed a view."""

    def test_cancel_does_not_describe_the_appointment_before_identity(self):
        """Time, provider and reason are not shown to whoever holds the link."""
        ctx = _ctx(run=_configured_run())
        token = make_action_token("run-1", "cancel")
        r = _call(
            client_(),
            "GET",
            f"/api/campaigns/link/cancel/appointment?token={token}",
            ctx,
        )
        body = r.json()
        assert body["identity_required"] is True
        assert body["appointment"] is None

    def test_cancel_is_refused_before_identity(self):
        """Refusing to describe it would be hollow if the POST still acted."""
        ctx = _ctx(run=_configured_run())
        token = make_action_token("run-1", "cancel")
        r = _call(
            client_(),
            "POST",
            f"/api/campaigns/link/cancel/appointment?token={token}",
            ctx,
        )
        assert r.status_code == 403
        assert r.json()["error"] == "identity_required"

    def test_reschedule_is_refused_before_identity(self):
        ctx = _ctx(run=_configured_run())
        token = make_action_token("run-1", "reschedule")
        r = _call(
            client_(),
            "POST",
            f"/api/campaigns/link/reschedule/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 403

    def test_booking_is_not_gated_by_default(self):
        """It discloses only the clinic's own free slots and can be undone."""
        ctx = _ctx(run=_configured_run())
        token = make_action_token("run-1", "book")
        r = _call(
            client_(),
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 200

    def test_always_gates_booking_too(self):
        ctx = _ctx(run=_configured_run(identity_check="always"))
        token = make_action_token("run-1", "book")
        r = _call(
            client_(),
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 403

    def _describe_stubbed(self, ctx, token):
        # These cases pass the gate, so the endpoint goes on to describe the
        # appointment. That query is not what is under test here.
        with patch(
            "src.app.api.routes.campaign_booking._describe_appointment",
            AsyncMock(return_value=None),
        ):
            return _call(
                client_(),
                "GET",
                f"/api/campaigns/link/cancel/appointment?token={token}",
                ctx,
            )

    def test_off_lets_a_cancel_through(self):
        """For a campaign whose audience was verified some other way."""
        ctx = _ctx(run=_configured_run(identity_check="off"))
        r = self._describe_stubbed(ctx, make_action_token("run-1", "cancel"))
        assert r.json()["identity_required"] is False

    def test_passing_the_gate_opens_the_action(self):
        ctx = _ctx(run=_configured_run(verified=True))
        r = self._describe_stubbed(ctx, make_action_token("run-1", "cancel"))
        assert r.json()["identity_required"] is False

    def test_a_run_from_before_the_setting_is_not_broken(self):
        """No booking_link step ran, so no author expressed a view. Turning a
        live link into a refusal the patient cannot clear is the worse
        failure."""
        ctx = _ctx(run=_run())  # no config at all
        r = self._describe_stubbed(ctx, make_action_token("run-1", "cancel"))
        assert r.json()["identity_required"] is False


class TestConfiguredProvider:
    def test_listing_slots_uses_the_provider_chosen_by_the_workflow(self):
        token = make_action_token("run-1", "book")
        ctx = _ctx(run=_configured_run(provider_id="prov-locked"))

        r = _call(
            client_(),
            "GET",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
        )

        assert r.status_code == 200
        _session, _cm, adapter, _b = ctx
        assert (
            adapter.find_available_slots.await_args.kwargs["provider_id"]
            == "prov-locked"
        )

    def test_booking_recheck_uses_the_provider_chosen_by_the_workflow(self):
        token = make_action_token("run-1", "book")
        ctx = _ctx(
            run=_configured_run(provider_id="prov-locked"),
            slots=[_slot(provider="prov-locked")],
        )

        r = _call(
            client_(),
            "POST",
            f"/api/campaigns/link/book/slots?token={token}",
            ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )

        assert r.status_code == 200
        _session, _cm, adapter, _b = ctx
        assert (
            adapter.find_available_slots.await_args.kwargs["provider_id"]
            == "prov-locked"
        )
