"""Reading and working the leads that landed.

The two things worth pinning down: the stage a clinic sees is derived from one
fact and cannot drift, and a lead's contact details are masked here exactly as a
patient's are — they belong to someone who is not a patient and has consented to
very little.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.api.deps import get_current_institution_admin
from src.app.api.routes.enquiries import _mask_email, _stage, router
from src.app.database import get_db_session_dep
from src.app.models.campaign_enquiry import CampaignEnquiry, EnquiryStatus

BASE = "/api/institution/enquiries"


def _user(institution_id="inst-1"):
    user = MagicMock()
    user.institution_id = institution_id
    return user


def _enquiry(**over):
    row = CampaignEnquiry(
        id="e1",
        institution_id="inst-1",
        intake_key="form-1",
        source="typeform",
        status=EnquiryStatus.NEW.value,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    row.first_name = "Dana"
    row.last_name = "Reyes"
    row.phone = "+15054821234"
    row.email = "dana@example.com"
    for key, value in over.items():
        setattr(row, key, value)
    return row


def _session(rows=None, one=None, total=None):
    session = AsyncMock()
    added: list = []
    session.add = MagicMock(side_effect=added.append)

    async def _flush():
        # A real flush applies the column defaults; a newly built enquiry has
        # no timestamps until then, and the response model requires them.
        for obj in added:
            for field in ("created_at", "updated_at"):
                if getattr(obj, field, None) is None:
                    setattr(obj, field, datetime.now(timezone.utc))

    session.flush = AsyncMock(side_effect=_flush)
    rows = rows if rows is not None else []
    queue = [total if total is not None else len(rows)]

    async def _execute(_stmt):
        result = MagicMock()
        result.scalar.return_value = queue.pop(0) if queue else 0
        result.scalars.return_value.all.return_value = rows
        # intake_enquiry looks people up with .scalars().first(); without this
        # it receives a MagicMock and treats it as an existing lead.
        result.scalars.return_value.first.return_value = one
        result.scalar_one_or_none.return_value = one
        return result

    session.execute = AsyncMock(side_effect=_execute)
    # Exposed so a test can inspect what was written without replacing
    # session.add, which would disconnect the flush above.
    session.added = added
    return session


def _client(session, user=None):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    from src.app.api.rate_limit import limiter

    app.state.limiter = limiter
    app.dependency_overrides[get_current_institution_admin] = lambda: user or _user()
    app.dependency_overrides[get_db_session_dep] = lambda: session
    return TestClient(app)


class TestStageIsDerived:
    def test_a_fresh_enquiry_is_a_lead(self):
        assert _stage(_enquiry()) == "lead"

    def test_a_worked_enquiry_is_contacted(self):
        assert _stage(_enquiry(status=EnquiryStatus.ENGAGED.value)) == "contacted"

    def test_a_pms_record_makes_them_registered(self):
        """The one fact that decides it."""
        assert _stage(_enquiry(contact_id="c-1")) == "registered"

    def test_booked_outranks_registered(self):
        """A booked lead must not read as merely registered."""
        row = _enquiry(contact_id="c-1", status=EnquiryStatus.BOOKED.value)
        assert _stage(row) == "booked"

    def test_it_cannot_drift_from_the_link(self):
        """Nothing stores the stage, so nothing can contradict it."""
        row = _enquiry(status=EnquiryStatus.QUALIFIED.value)
        assert _stage(row) == "contacted"
        row.contact_id = "c-9"
        assert _stage(row) == "registered"


class TestMasking:
    def test_an_email_keeps_only_its_first_letter_and_domain(self):
        assert _mask_email("dana@example.com") == "d***@example.com"

    def test_a_one_character_local_part_is_still_masked(self):
        assert _mask_email("d@example.com") == "d*@example.com"

    def test_junk_is_not_paraded_as_an_address(self):
        assert _mask_email("not-an-email") is None
        assert _mask_email(None) is None

    def test_the_list_never_carries_a_full_phone_or_email(self):
        session = _session(rows=[_enquiry()])
        r = _client(session).get(BASE)
        assert r.status_code == 200
        body = r.text
        assert "5054821234" not in body
        assert "dana@example.com" not in body
        item = r.json()["items"][0]
        assert item["phone_masked"].endswith("1234")
        assert item["email_masked"] == "d***@example.com"


class TestListing:
    def test_it_reports_the_true_total_not_the_page_size(self):
        session = _session(rows=[_enquiry()], total=137)
        r = _client(session).get(f"{BASE}?limit=1")
        assert r.json()["total"] == 137

    def test_the_stage_filter_narrows_the_page(self):
        rows = [_enquiry(id="a"), _enquiry(id="b", contact_id="c-1")]
        session = _session(rows=rows)
        r = _client(session).get(f"{BASE}?stage=registered")
        ids = [i["id"] for i in r.json()["items"]]
        assert ids == ["b"]

    def test_notes_are_flagged_but_not_returned_in_the_list(self):
        """Presence is useful at a glance; the content is not the list's job."""
        row = _enquiry()
        row.notes = "Rang twice, no answer"
        session = _session(rows=[row])
        item = _client(session).get(BASE).json()["items"][0]
        assert item["has_notes"] is True
        assert "Rang twice" not in _client(_session(rows=[row])).get(BASE).text

    def test_an_oversized_page_is_refused(self):
        r = _client(_session()).get(f"{BASE}?limit=5000")
        assert r.status_code == 422

    def test_a_user_with_no_institution_is_refused(self):
        r = _client(_session(), user=_user(institution_id=None)).get(BASE)
        assert r.status_code == 400


class TestWorkingALead:
    def test_notes_can_be_written_and_read_back(self):
        row = _enquiry()
        session = _session(one=row)
        r = _client(session).patch(f"{BASE}/e1", json={"notes": "Rang twice"})
        assert r.status_code == 200
        assert r.json()["notes"] == "Rang twice"
        assert row.notes == "Rang twice"

    def test_notes_are_encrypted_at_rest(self):
        row = _enquiry()
        _client(_session(one=row)).patch(f"{BASE}/e1", json={"notes": "implant consult"})
        assert "implant" not in (row.notes_encrypted or "")

    def test_a_status_only_update_does_not_wipe_the_notes(self):
        """None means 'not supplied'. Losing a staff note to an unrelated edit
        is the kind of thing nobody reports and everybody resents."""
        row = _enquiry()
        row.notes = "Important context"
        session = _session(one=row)
        _client(session).patch(f"{BASE}/e1", json={"status": "engaged"})
        assert row.notes == "Important context"
        assert row.status == "engaged"

    def test_an_empty_string_clears_the_notes(self):
        row = _enquiry()
        row.notes = "stale"
        _client(_session(one=row)).patch(f"{BASE}/e1", json={"notes": ""})
        assert row.notes is None

    def test_an_unknown_status_is_refused(self):
        row = _enquiry()
        r = _client(_session(one=row)).patch(f"{BASE}/e1", json={"status": "vibes"})
        assert r.status_code == 422
        assert row.status == EnquiryStatus.NEW.value

    def test_another_clinics_lead_is_not_found(self):
        r = _client(_session(one=None)).patch(f"{BASE}/e999", json={"notes": "x"})
        assert r.status_code == 404

    def test_the_detail_returns_the_note_in_full(self):
        """A note nobody can read is not a note."""
        row = _enquiry()
        row.notes = "Rang twice, wants Saturday"
        r = _client(_session(one=row)).get(f"{BASE}/e1")
        assert r.json()["notes"] == "Rang twice, wants Saturday"


class TestManualEntry:
    """Staff take enquiries that never touch a form — somebody rings, or walks in."""

    def _post(self, session, body):
        return _client(session).post(BASE, json=body)

    def test_a_lead_can_be_entered_by_hand(self):
        session = _session(one=None)
        r = self._post(session, {"first_name": "Dana", "phone": "+15054821234"})
        assert r.status_code == 201
        assert r.json()["created"] is True

    def test_it_is_marked_as_manual_not_as_a_form(self):
        """So a clinic can tell which of its channels actually produces leads."""
        session = _session(one=None)
        r = self._post(session, {"phone": "+15054821234"})
        assert r.json()["enquiry"]["source"] == "manual"

    def test_someone_with_no_way_to_be_reached_is_refused(self):
        r = self._post(_session(one=None), {"first_name": "Dana"})
        assert r.status_code == 422

    def test_an_email_only_lead_is_accepted(self):
        r = self._post(_session(one=None), {"email": "dana@example.com"})
        assert r.status_code == 201

    def test_notes_can_be_captured_at_entry(self):
        """The reason the call is being written down at all."""
        session = _session(one=None)
        r = self._post(session, {"phone": "+15054821234", "notes": "Asked about implants"})
        assert r.json()["enquiry"]["notes"] == "Asked about implants"

    def test_nothing_is_claimed_about_consent_that_was_not_ticked(self):
        """A staff member who did not ask must not imply it by saving a form."""
        from src.app.models.sms_consent import ConsentRecord

        session = _session(one=None)
        self._post(session, {"phone": "+15054821234"})
        assert [o for o in session.added if isinstance(o, ConsentRecord)] == []

    def test_a_declared_opt_in_is_recorded(self):
        from src.app.models.sms_consent import ConsentRecord

        session = _session(one=None)
        self._post(session, {
            "phone": "+15054821234", "consent_sms": True,
            "consent_wording": "Said yes on the phone",
        })
        records = [o for o in session.added if isinstance(o, ConsentRecord)]
        assert len(records) == 1
        assert records[0].reason == "Said yes on the phone"

    def test_an_existing_lead_is_reported_rather_than_duplicated(self):
        """Surfaced, not silent: whoever typed it needs to know why no new row
        appeared, or they will type it again."""
        existing = _enquiry(id="already-here")
        session = _session(one=existing)
        r = self._post(session, {"phone": "+15054821234"})
        assert r.status_code == 201
        assert r.json()["created"] is False
        assert r.json()["enquiry"]["id"] == "already-here"

    def test_a_user_with_no_institution_is_refused(self):
        r = _client(_session(), user=_user(institution_id=None)).post(
            BASE, json={"phone": "+15054821234"}
        )
        assert r.status_code == 400
