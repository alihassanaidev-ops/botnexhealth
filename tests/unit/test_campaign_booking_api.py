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
from src.app.services.automation.campaign_action_links import make_action_token


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _expired(action: str = "book") -> str:
    return make_action_token("run-1", action, ttl_seconds=1, now=int(time.time()) - 9999)


def _run():
    run = MagicMock()
    run.id = "run-1"
    run.institution_id = "inst-1"
    run.location_id = "loc-1"
    run.workflow_id = "wf-1"
    run.contact_id = "c-1"
    run.trigger_metadata = {}
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


def _ctx(*, run=None, contact_pms_id="pms-9", already_booked=False, slots=None,
         book_success=True, write_status=BookingWriteStatus.CONFIRMED.value):
    """Patch the module's collaborators for one request."""
    session = AsyncMock()
    run = run if run is not None else _run()
    contact = MagicMock(nexhealth_patient_id=contact_pms_id)
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

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    adapter = AsyncMock()
    adapter.find_available_slots = AsyncMock(
        return_value=MagicMock(slots=slots if slots is not None else [_slot()])
    )
    adapter.book_appointment = AsyncMock(
        return_value=MagicMock(success=book_success, write_status=write_status)
    )

    return session, _Ctx(), adapter, AsyncMock(return_value=already_booked)


def _call(client, method, path, ctx, json=None):
    session, cm, adapter, booked = ctx
    with patch(
        "src.app.api.routes.campaign_booking.get_system_db_session", return_value=cm
    ), patch(
        "src.app.api.routes.campaign_booking.get_adapter_for_institution_location",
        AsyncMock(return_value=adapter),
    ), patch(
        "src.app.api.routes.campaign_booking._already_booked", booked
    ), patch(
        "src.app.api.routes.campaign_booking.log_audit_background"
    ):
        return client.request(method, path, json=json)


class TestTokenGate:
    def test_a_forged_token_gets_nothing(self, client):
        r = _call(client, "GET", "/api/campaigns/link/book/slots?token=a.book.9.bad", _ctx())
        assert r.status_code == 400

    def test_an_expired_token_says_so(self, client):
        r = _call(client, "GET", f"/api/campaigns/link/book/slots?token={_expired()}", _ctx())
        assert r.status_code == 410

    def test_confirm_cannot_reach_the_slot_picker(self, client):
        """Only book and reschedule pick slots; confirm needs no slot."""
        token = make_action_token("run-1", "confirm")
        r = _call(client, "GET", f"/api/campaigns/link/confirm/slots?token={token}", _ctx())
        assert r.status_code == 404

    def test_a_book_token_cannot_be_replayed_on_reschedule(self, client):
        token = make_action_token("run-1", "book")
        r = _call(client, "GET", f"/api/campaigns/link/reschedule/slots?token={token}", _ctx())
        assert r.status_code == 400

    def test_every_response_sends_no_referrer(self, client):
        r = _call(client, "GET", "/api/campaigns/link/book/slots?token=junk", _ctx())
        assert r.headers["referrer-policy"] == "no-referrer"


class TestListingSlots:
    def test_slots_are_offered_with_no_patient_detail(self, client):
        token = make_action_token("run-1", "book")
        r = _call(client, "GET", f"/api/campaigns/link/book/slots?token={token}", _ctx())
        assert r.status_code == 200
        body = r.json()
        assert body["slots"][0]["start"] == "2026-09-02T14:00:00Z"
        # nothing identifying the patient, the run or the clinic's internals
        for leak in ("run-1", "c-1", "inst-1", "pms-9"):
            assert leak not in r.text

    def test_an_already_booked_run_is_not_offered_more_slots(self, client):
        token = make_action_token("run-1", "book")
        r = _call(
            client, "GET", f"/api/campaigns/link/book/slots?token={token}",
            _ctx(already_booked=True),
        )
        assert r.json()["already_booked"] is True
        assert r.json()["slots"] == []


class TestBooking:
    def test_booking_the_offered_slot_succeeds(self, client):
        token = make_action_token("run-1", "book")
        ctx = _ctx()
        r = _call(
            client, "POST", f"/api/campaigns/link/book/slots?token={token}", ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "booked"
        _session, _cm, adapter, _b = ctx
        adapter.book_appointment.assert_awaited_once()

    def test_the_client_cannot_choose_the_booking_parameters(self, client):
        """It sends a start time; the server books the slot it found itself."""
        token = make_action_token("run-1", "book")
        ctx = _ctx(slots=[_slot(provider="the-real-provider")])
        _call(
            client, "POST", f"/api/campaigns/link/book/slots?token={token}", ctx,
            json={"slot_start": "2026-09-02T14:00:00Z", "provider_id": "attacker-choice"},
        )
        _session, _cm, adapter, _b = ctx
        booked = adapter.book_appointment.await_args.args[0]
        assert booked.provider_id == "the-real-provider"

    def test_a_time_that_is_not_on_offer_is_refused(self, client):
        """Covers both a crafted request and a slot taken while deciding."""
        token = make_action_token("run-1", "book")
        ctx = _ctx()
        r = _call(
            client, "POST", f"/api/campaigns/link/book/slots?token={token}", ctx,
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
            client, "POST", f"/api/campaigns/link/book/slots?token={token}", ctx,
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.json()["status"] == "already_booked"
        _session, _cm, adapter, _b = ctx
        adapter.book_appointment.assert_not_awaited()

    def test_a_gotracker_pending_write_is_not_reported_as_booked(self, client):
        """Item 4 again: accepted is not the same as in the practice software."""
        token = make_action_token("run-1", "book")
        r = _call(
            client, "POST", f"/api/campaigns/link/book/slots?token={token}",
            _ctx(write_status=BookingWriteStatus.PENDING.value),
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.json()["status"] == "pending"

    def test_a_rejected_booking_does_not_claim_success(self, client):
        token = make_action_token("run-1", "book")
        r = _call(
            client, "POST", f"/api/campaigns/link/book/slots?token={token}",
            _ctx(book_success=False),
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 502

    def test_a_patient_with_no_practice_record_is_handed_off(self, client):
        token = make_action_token("run-1", "book")
        r = _call(
            client, "POST", f"/api/campaigns/link/book/slots?token={token}",
            _ctx(contact_pms_id=None),
            json={"slot_start": "2026-09-02T14:00:00Z"},
        )
        assert r.status_code == 409
        assert r.json()["error"] == "handoff"
