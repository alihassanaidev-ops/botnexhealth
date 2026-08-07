"""Unit tests for the server-side identity gate on lookup_patient.

The Retell prompt's identity check is treated as advisory only — the
server must independently verify the caller-supplied DOB matches the
matched patient and that a second factor (exact email or full phone number)
corroborates. Otherwise the response is downgraded to ``basic``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.retell import handlers


def _ctx(patient: SimpleNamespace, location: SimpleNamespace | None = None):
    """Build a fake _resolve_context result around a single PMS patient."""
    adapter = MagicMock()
    adapter.search_patients = AsyncMock(return_value=[patient])
    return SimpleNamespace(
        institution=SimpleNamespace(id="11111111-1111-1111-1111-111111111111"),
        location=location,
        adapter=adapter,
    )


def _patient(
    *,
    pid: str = "p1",
    dob: str | None = "1990-01-01",
    email: str | None = "alice@example.com",
    phone: str | None = "+15551234567",
):
    return SimpleNamespace(
        id=pid,
        first_name="Alice",
        last_name="Doe",
        email=email,
        phone=phone,
        date_of_birth=dob,
        extra={"upcoming_appointments": [{"id": "appt-1"}]},
    )


def test_full_patient_payload_excludes_procedures_and_insurance():
    patient = _patient()
    patient.extra.update(
        {
            "recent_procedures": [{"id": "procedure-1"}],
            "insurance_coverages": [{"id": "coverage-1"}],
            "last_visit": {"id": "appointment-previous"},
        }
    )

    payload = handlers._to_full_patient_payload(patient)

    assert payload["upcoming_appointments"] == [{"id": "appt-1"}]
    assert payload["last_visit"] == {"id": "appointment-previous"}
    assert "recent_procedures" not in payload
    assert "insurance_coverages" not in payload


@pytest.mark.asyncio
async def test_identity_gate_allows_full_when_dob_and_email_match(monkeypatch):
    p = _patient()
    ctx = _ctx(p)

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)
    target = handlers.lookup_patient.__wrapped__  # bypass audit decorator

    result = await target(
        {
            "name": "Alice",
            "date_of_birth": "1990-01-01",
            "email": "alice@example.com",
            "detail_level": "full",
        }
    )

    assert result["detail_level"] == "full"
    assert "identity_gate" not in result
    # full payload includes email/phone in clear
    assert result["patients"][0]["email"] == "alice@example.com"


@pytest.mark.asyncio
async def test_identity_gate_allows_full_with_matching_phone(monkeypatch):
    p = _patient()
    ctx = _ctx(p)

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)
    target = handlers.lookup_patient.__wrapped__

    result = await target(
        {
            "name": "Alice",
            "date_of_birth": "1990-01-01",
            "phone_number": "(555) 123-4567",
            "detail_level": "full",
        }
    )

    assert result["detail_level"] == "full"


@pytest.mark.asyncio
async def test_identity_gate_rejects_phone_with_only_matching_last4(monkeypatch):
    p = _patient()
    ctx = _ctx(p)

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)
    target = handlers.lookup_patient.__wrapped__

    result = await target(
        {
            "name": "Alice",
            "date_of_birth": "1990-01-01",
            "phone_number": "(415) 555-4567",
            "detail_level": "full",
        }
    )

    assert result["detail_level"] == "basic"
    assert result["identity_gate"] == "second_factor_mismatch"


@pytest.mark.asyncio
async def test_identity_gate_demotes_to_basic_when_name_missing(monkeypatch):
    p = _patient()
    ctx = _ctx(p)

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)
    target = handlers.lookup_patient.__wrapped__

    result = await target(
        {
            "date_of_birth": "1990-01-01",
            "phone_number": "+15551234567",
            "detail_level": "full",
        }
    )

    assert result["detail_level"] == "basic"
    assert result["identity_gate"] == "missing_name"


@pytest.mark.asyncio
async def test_identity_gate_demotes_to_basic_when_dob_missing(monkeypatch):
    p = _patient()
    ctx = _ctx(p)

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)
    target = handlers.lookup_patient.__wrapped__

    result = await target(
        {
            "name": "Alice",
            "email": "alice@example.com",
            "detail_level": "full",  # asked for full, no DOB supplied
        }
    )

    assert result["detail_level"] == "basic"
    assert result["identity_gate"] == "missing_dob"
    # basic payload masks email and phone
    assert "email" not in result["patients"][0]
    assert result["patients"][0].get("email_hint") is not None


@pytest.mark.asyncio
async def test_identity_gate_demotes_when_dob_does_not_match(monkeypatch):
    p = _patient(dob="1990-01-01")
    ctx = _ctx(p)

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)
    target = handlers.lookup_patient.__wrapped__

    result = await target(
        {
            "name": "Alice",
            "date_of_birth": "1980-05-05",  # wrong DOB
            "email": "alice@example.com",
            "detail_level": "full",
        }
    )

    assert result["detail_level"] == "basic"
    assert result["identity_gate"] == "dob_mismatch"


@pytest.mark.asyncio
async def test_identity_gate_demotes_when_only_dob_supplied(monkeypatch):
    p = _patient()
    ctx = _ctx(p)

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)
    target = handlers.lookup_patient.__wrapped__

    result = await target(
        {
            "name": "Alice",
            "date_of_birth": "1990-01-01",  # right DOB but no second factor
            "detail_level": "full",
        }
    )

    assert result["detail_level"] == "basic"
    assert result["identity_gate"] == "second_factor_missing"


@pytest.mark.asyncio
async def test_multiple_matches_force_basic_regardless_of_identity_input(monkeypatch):
    p1 = _patient(pid="p1")
    p2 = _patient(pid="p2", email="alice2@example.com")

    adapter = MagicMock()
    adapter.search_patients = AsyncMock(return_value=[p1, p2])
    ctx = SimpleNamespace(
        institution=SimpleNamespace(id="i1"),
        location=None,
        adapter=adapter,
    )

    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)
    target = handlers.lookup_patient.__wrapped__

    result = await target(
        {
            "name": "Alice",
            "date_of_birth": "1990-01-01",
            "email": "alice@example.com",
            "detail_level": "full",
        }
    )

    assert result["detail_level"] == "basic"
    assert result["disambiguation_required"] is True
