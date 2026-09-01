"""Taking in a lead: identity when we barely know them, and consent.

A lead is the only person in this system who is not a patient. These tests are
mostly about the two things that makes awkward — recognising someone we already
have, and being able to prove we were allowed to contact them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.models.contact import LeadStatus
from src.app.models.contact import Contact
from src.app.models.sms_consent import ConsentBasis, ConsentRecord, ConsentStatus
from src.app.services.automation.enquiry_intake_service import intake_enquiry
from src.app.services.sms_privacy import hash_email, hash_phone


class _Session:
    """Returns queued results in order, and remembers what was added."""

    def __init__(self, results=None):
        self._results = list(results or [])
        self.added: list = []
        self.flush = AsyncMock()

    async def execute(self, _stmt):
        result = MagicMock()
        value = self._results.pop(0) if self._results else None
        result.scalar_one_or_none.return_value = value
        result.scalars.return_value.first.return_value = value
        return result

    def add(self, obj):
        self.added.append(obj)


def _added(session, cls):
    return [o for o in session.added if isinstance(o, cls)]


BASE = dict(institution_id="inst-1", intake_key="form-1", source="webhook")


@pytest.mark.asyncio
class TestRecordingALead:
    async def test_a_new_lead_is_stored_with_both_hashes(self):
        session = _Session()
        result = await intake_enquiry(
            session, **BASE, phone="+1 (505) 482-1234", email="Dana@example.com"
        )
        assert result.created
        e = result.enquiry
        assert e.phone_hash == hash_phone("+1 (505) 482-1234")
        assert e.email_hash == hash_email("Dana@example.com")
        assert e.lead_status == LeadStatus.NEW.value

    async def test_a_lead_can_arrive_with_only_an_email(self):
        """A form that asks for nothing else still has to be workable."""
        session = _Session()
        result = await intake_enquiry(session, **BASE, email="dana@example.com")
        assert result.created
        assert result.enquiry.email_hash
        assert result.enquiry.phone_hash is None

    async def test_attribution_and_external_ref_are_kept(self):
        session = _Session()
        result = await intake_enquiry(
            session, **BASE, phone="5054821234",
            attribution={"utm_source": "google", "form_id": "contact-us"},
            external_ref="ghl-9931",
        )
        assert result.enquiry.attribution["utm_source"] == "google"
        assert result.enquiry.external_ref == "ghl-9931"

    async def test_notes_are_encrypted_not_stored_in_the_clear(self):
        session = _Session()
        result = await intake_enquiry(
            session, **BASE, phone="5054821234", notes="Asked about implants"
        )
        assert result.enquiry.notes == "Asked about implants"
        assert "implants" not in (result.enquiry.notes_encrypted or "")


@pytest.mark.asyncio
class TestRecognisingSomeoneWeHave:
    async def test_the_same_intake_key_does_not_create_a_second(self):
        existing = Contact(id="e1", institution_id="inst-1",
                           intake_key="form-1", lead_source="webhook")
        session = _Session([existing])
        result = await intake_enquiry(session, **BASE, phone="5054821234")
        assert not result.created
        assert result.enquiry is existing

    async def test_the_same_phone_through_a_different_form_is_matched(self):
        """Email-only dedup misses this; it is the common case."""
        existing = Contact(id="e1", institution_id="inst-1",
                           intake_key="other", lead_source="webhook")
        session = _Session([None, existing])
        result = await intake_enquiry(
            session, institution_id="inst-1", intake_key="form-2",
            source="webhook", phone="5054821234",
        )
        assert not result.created

    async def test_a_resubmission_fills_blanks_without_flattening_them(self):
        """A repost may know a phone the first did not — but must not wipe
        anything a person has since entered."""
        existing = Contact(id="e1", institution_id="inst-1",
                           intake_key="form-1", lead_source="webhook")
        existing.first_name = "Dana"
        session = _Session([existing])
        await intake_enquiry(
            session, **BASE, first_name="D", last_name="Reyes", phone="5054821234"
        )
        assert existing.first_name == "Dana"   # not overwritten
        assert existing.last_name == "Reyes"   # filled in

    async def test_a_lead_who_is_already_a_patient_is_returned_not_duplicated(self):
        """Collapsing the tables is what makes this one lookup instead of two.

        The enquiry used to be compared only against other enquiries, so
        somebody the practice had known for years arrived as new and then
        needed a second query to notice. Now the same match finds them.
        """
        patient = Contact(id="c-9", institution_id="inst-1")
        patient.nexhealth_patient_id = "nh-42"
        patient.phone = "5054821234"
        session = _Session([None, patient])
        result = await intake_enquiry(session, **BASE, phone="5054821234")

        assert not result.created
        assert result.matched_existing_contact
        assert result.enquiry is patient
        # Untouched: an enquiry must not demote an existing patient to a lead.
        assert result.enquiry.nexhealth_patient_id == "nh-42"
        assert result.enquiry.lead_status is None


@pytest.mark.asyncio
class TestConsent:
    async def test_nothing_is_recorded_when_the_form_did_not_ask(self):
        """Submitting a form is not consent to be texted."""
        session = _Session()
        await intake_enquiry(session, **BASE, phone="5054821234")
        assert _added(session, ConsentRecord) == []

    async def test_an_opt_in_is_recorded_where_the_send_gates_look(self):
        session = _Session()
        await intake_enquiry(
            session, **BASE, phone="5054821234",
            consent_channels=("sms",), consent_wording="I agree to receive texts",
        )
        records = _added(session, ConsentRecord)
        assert len(records) == 1
        assert records[0].channel == "sms"
        assert records[0].status == ConsentStatus.GRANTED.value
        assert records[0].phone_hash == hash_phone("5054821234")

    async def test_consent_is_express_never_implied(self):
        """Implied is for an existing relationship. A lead has none."""
        session = _Session()
        await intake_enquiry(
            session, **BASE, phone="5054821234", consent_channels=("sms",)
        )
        assert _added(session, ConsentRecord)[0].basis == ConsentBasis.EXPRESS.value

    async def test_it_is_keyed_by_hash_because_there_is_no_contact_yet(self):
        session = _Session()
        await intake_enquiry(
            session, **BASE, phone="5054821234", consent_channels=("sms",)
        )
        assert _added(session, ConsentRecord)[0].contact_id is None

    async def test_the_wording_shown_is_kept_as_the_evidence(self):
        session = _Session()
        await intake_enquiry(
            session, **BASE, phone="5054821234",
            consent_channels=("sms",), consent_wording="Text me about appointments",
        )
        assert _added(session, ConsentRecord)[0].reason == "Text me about appointments"

    async def test_sms_consent_is_not_recorded_without_a_phone(self):
        """An email-only lead cannot have consented to be texted."""
        session = _Session()
        await intake_enquiry(
            session, **BASE, email="dana@example.com", consent_channels=("sms", "email")
        )
        channels = {r.channel for r in _added(session, ConsentRecord)}
        assert channels == {"email"}
