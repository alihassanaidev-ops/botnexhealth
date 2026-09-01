"""Contacts and Patients are two projections of one person identity."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.api.routes import contacts as route
from src.app.models.contact import Contact
from src.app.models.user import UserRole
from src.app.services.automation.enquiry_intake_service import IntakeResult


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._rows


class _Session:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.commit = AsyncMock()

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0) if self.results else _Result()


class _Context:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


def _user(*, role=UserRole.INSTITUTION_ADMIN.value, location_id=None):
    return SimpleNamespace(
        id="user-1",
        role=role,
        institution_id="inst-1",
        location_id=location_id,
    )


def _contact(**values):
    row = Contact(
        id=values.pop("id", "contact-1"),
        institution_id="inst-1",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        **values,
    )
    return row


def _detail(contact_id="contact-1"):
    return route.ContactDetail(
        id=contact_id,
        full_name="Dana Reyes",
        first_name="Dana",
        last_name="Reyes",
        is_new_patient=True,
        lifecycle="lead",
        lead_status="new",
        source="manual",
        email_masked="d***@example.com",
        notes=None,
        phone_masked="+*******1234",
        phone_reveal_available=True,
        created_at=datetime.now(timezone.utc).isoformat(),
        aliases=[],
        calls=[],
        call_count=0,
    )


def test_lifecycle_is_a_projection_not_a_second_person_record():
    lead = _contact(lead_status="new")
    caller = _contact(id="caller-1")
    patient = _contact(id="patient-1", lead_status="engaged", nexhealth_patient_id="pms-9")

    assert route._lifecycle(lead) == "lead"
    assert route._lifecycle(caller) == "contact"
    assert route._lifecycle(patient) == "patient"


@pytest.mark.asyncio
async def test_patient_directory_adds_the_pms_link_filter(monkeypatch):
    session = _Session([_Result(scalar=0), _Result(rows=[])])
    monkeypatch.setattr(route, "get_db_session", lambda: _Context(session))

    original = inspect.unwrap(route.list_contacts)
    result = await original(
        request=MagicMock(),
        current_user=_user(),
        limit=25,
        offset=0,
        directory="patients",
        lifecycle=None,
        search=None,
    )

    assert result.total == 0
    sql = str(session.statements[0])
    assert "contacts.nexhealth_patient_id IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_location_admin_creation_is_pinned_and_granted_to_their_location(monkeypatch):
    session = _Session([_Result(scalar="loc-1"), _Result()])
    monkeypatch.setattr(route, "get_db_session", lambda: _Context(session))
    captured = {}

    async def _intake(_session, **kwargs):
        captured.update(kwargs)
        return IntakeResult(enquiry=_contact(), created=True)

    monkeypatch.setattr(route, "intake_enquiry", _intake)
    monkeypatch.setattr(route, "_load_contact_detail", AsyncMock(return_value=_detail()))

    original = inspect.unwrap(route.create_contact)
    response = await original(
        request=MagicMock(),
        data=route.ContactCreateRequest(
            phone="+15054821234",
            location_id="a-location-the-user-cannot-choose",
            consent_sms=True,
            consent_email=True,
            consent_wording="Agreed to SMS and email",
        ),
        current_user=_user(role=UserRole.LOCATION_ADMIN.value, location_id="loc-1"),
    )

    assert response.created is True
    assert captured["location_id"] == "loc-1"
    assert captured["consent_channels"] == ("sms", "email")
    grant_sql = str(session.statements[1])
    assert "contact_location_accesses" in grant_sql
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_manual_contact_requires_a_reachable_identifier():
    original = inspect.unwrap(route.create_contact)
    with pytest.raises(route.HTTPException) as exc:
        await original(
            request=MagicMock(),
            data=route.ContactCreateRequest(first_name="Dana"),
            current_user=_user(),
        )
    assert exc.value.status_code == 422

