"""Lead-to-patient registration behind a campaign action link.

Creating a real record in a clinic's practice software from an unauthenticated
web form is the most consequential thing any of these links do, so most of what
is asserted here is what the endpoint *refuses*.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.api.routes.campaign_registration import router
from src.app.services.automation.campaign_action_links import (
    REGISTRATION_CONFIG_KEY,
    make_action_token,
)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _token(action: str = "register", run_id: str = "run-1") -> str:
    return make_action_token(run_id, action)


def _expired() -> str:
    return make_action_token(
        "run-1", "register", ttl_seconds=1, now=int(time.time()) - 9999
    )


def _body(**over):
    return {"date_of_birth": "1988-04-02", "gender": "Female", **over}


class TestTokenHandling:
    """These run before any database work, so they need no fixtures."""

    def test_a_forged_token_gets_nothing(self, client):
        r = client.post("/api/campaigns/link/register?token=nonsense", json=_body())
        assert r.status_code == 400

    def test_an_expired_token_says_so(self, client):
        """410 not 400: the patient can act on 'your link ran out'."""
        r = client.post(f"/api/campaigns/link/register?token={_expired()}", json=_body())
        assert r.status_code == 410

    def test_a_booking_token_cannot_register_a_patient(self, client):
        """The action is inside the signed payload for exactly this reason."""
        r = client.post(
            f"/api/campaigns/link/register?token={_token('book')}", json=_body()
        )
        assert r.status_code == 400

    def test_responses_send_no_referrer(self, client):
        """The token is in the URL; it must not leak via Referer."""
        r = client.post("/api/campaigns/link/register?token=bad", json=_body())
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert r.headers["Cache-Control"] == "no-store"


class TestInputValidation:
    """Rejected before the PMS is ever called."""

    @pytest.mark.parametrize(
        "dob", ["02/04/1988", "1988-4-2", "", "not-a-date", "1988-04-02T00:00:00"]
    )
    def test_a_malformed_date_of_birth_is_refused(self, client, dob):
        r = client.post(
            f"/api/campaigns/link/register?token={_token()}",
            json=_body(date_of_birth=dob),
        )
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_date_of_birth"

    @pytest.mark.parametrize("gender", ["female", "F", "", "Unspecified"])
    def test_a_gender_outside_the_pms_contract_is_refused(self, client, gender):
        r = client.post(
            f"/api/campaigns/link/register?token={_token()}", json=_body(gender=gender)
        )
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_gender"


def _session_with(contact, run, institution=MagicMock(), location=MagicMock()):
    """A session whose .get returns the right object for each model."""
    from src.app.models.automation_workflow import AutomationWorkflowRun
    from src.app.models.contact import Contact
    from src.app.models.institution import Institution
    from src.app.models.institution_location import InstitutionLocation

    mapping = {
        AutomationWorkflowRun: run,
        Contact: contact,
        Institution: institution,
        InstitutionLocation: location,
    }

    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, _id: mapping.get(model))
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _run(config=None):
    run = MagicMock()
    run.id = "run-1"
    run.institution_id = "inst-1"
    run.location_id = "loc-1"
    run.workflow_id = "wf-1"
    run.contact_id = "contact-1"
    run.trigger_metadata = (
        {REGISTRATION_CONFIG_KEY: config} if config is not None else None
    )
    return run


def _contact(pms_id=None):
    contact = MagicMock()
    contact.nexhealth_patient_id = pms_id
    contact.first_name = "Dana"
    contact.last_name = "Reyes"
    contact.email = "dana@example.com"
    contact.phone = "+15550001111"
    contact.date_of_birth = None
    return contact


class TestRegistration:
    def _post(self, client, session, adapter, body=None):
        with patch(
            "src.app.api.routes.campaign_registration.get_system_db_session"
        ) as db, patch(
            "src.app.api.routes.campaign_registration."
            "get_adapter_for_institution_location",
            AsyncMock(return_value=adapter),
        ):
            db.return_value.__aenter__.return_value = session
            db.return_value.__aexit__.return_value = False
            return client.post(
                f"/api/campaigns/link/register?token={_token()}",
                json=body or _body(),
            )

    def test_a_lead_becomes_a_patient_and_the_contact_is_linked(self, client):
        contact = _contact()
        session = _session_with(contact, _run({"provider_id": "prov-7"}))
        adapter = MagicMock()
        adapter.create_patient = AsyncMock(
            return_value={"success": True, "patient_id": "nh-4242", "message": "ok"}
        )

        r = self._post(client, session, adapter)

        assert r.status_code == 200
        assert r.json()["status"] == "registered"
        # The whole point: the booking step that follows has something to book.
        assert contact.nexhealth_patient_id == "nh-4242"

    def test_the_configured_provider_is_used(self, client):
        """provider_id is a clinic decision, never the patient's."""
        contact = _contact()
        session = _session_with(contact, _run({"provider_id": "prov-7"}))
        adapter = MagicMock()
        adapter.create_patient = AsyncMock(
            return_value={"success": True, "patient_id": "nh-1", "message": "ok"}
        )

        self._post(client, session, adapter)

        assert adapter.create_patient.await_args.args[0].provider_id == "prov-7"

    def test_details_the_patient_corrects_win_over_the_campaign_copy(self, client):
        contact = _contact()
        session = _session_with(contact, _run({"provider_id": "prov-7"}))
        adapter = MagicMock()
        adapter.create_patient = AsyncMock(
            return_value={"success": True, "patient_id": "nh-1", "message": "ok"}
        )

        self._post(client, session, adapter, _body(email="new@example.com"))

        assert adapter.create_patient.await_args.args[0].email == "new@example.com"

    def test_an_already_linked_contact_is_not_duplicated(self, client):
        """Reopening the link must not create a second record for one person."""
        contact = _contact(pms_id="nh-existing")
        session = _session_with(contact, _run({"provider_id": "prov-7"}))
        adapter = MagicMock()
        adapter.create_patient = AsyncMock()

        r = self._post(client, session, adapter)

        assert r.json()["status"] == "already_registered"
        adapter.create_patient.assert_not_awaited()

    def test_no_configured_provider_refuses_rather_than_guessing(self, client):
        contact = _contact()
        session = _session_with(contact, _run(None))
        adapter = MagicMock()
        adapter.create_patient = AsyncMock()

        r = self._post(client, session, adapter)

        assert r.status_code == 503
        adapter.create_patient.assert_not_awaited()

    def test_a_refusal_from_the_practice_software_leaves_the_contact_unlinked(
        self, client
    ):
        contact = _contact()
        session = _session_with(contact, _run({"provider_id": "prov-7"}))
        adapter = MagicMock()
        adapter.create_patient = AsyncMock(
            return_value={"success": False, "patient_id": None, "message": "rejected"}
        )

        r = self._post(client, session, adapter)

        assert r.status_code == 502
        assert contact.nexhealth_patient_id is None

    def test_the_pms_error_text_is_never_echoed_back(self, client):
        """PMS errors routinely repeat the submitted payload — this patient's PHI."""
        contact = _contact()
        session = _session_with(contact, _run({"provider_id": "prov-7"}))
        adapter = MagicMock()
        adapter.create_patient = AsyncMock(
            side_effect=RuntimeError("Patient Dana Reyes 1988-04-02 already exists")
        )

        r = self._post(client, session, adapter)

        assert r.status_code == 503
        assert "Dana" not in r.text
        assert "1988" not in r.text
