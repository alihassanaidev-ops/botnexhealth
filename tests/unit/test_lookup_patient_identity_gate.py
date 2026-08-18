"""Regression tests for the privacy-safe Retell patient identity gate."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.retell import handlers


def _ctx(
    patients: list[SimpleNamespace], location: SimpleNamespace | None = None
) -> SimpleNamespace:
    adapter = MagicMock()
    adapter.search_patients = AsyncMock(return_value=patients)
    return SimpleNamespace(
        institution=SimpleNamespace(id="11111111-1111-1111-1111-111111111111"),
        location=location,
        adapter=adapter,
    )


def _patient(
    *,
    pid: str = "p1",
    first_name: str = "Alice",
    last_name: str = "Doe",
    dob: str | None = "1990-01-01",
    email: str | None = "alice@example.com",
    phone: str | None = "+15551234567",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=pid,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        date_of_birth=dob,
        extra={"upcoming_appointments": [{"id": "appt-1"}]},
    )


async def _invoke(monkeypatch: pytest.MonkeyPatch, ctx: SimpleNamespace, args: dict):
    async def _fake_resolve():
        return ctx

    monkeypatch.setattr(handlers, "_resolve_context", _fake_resolve)
    return await handlers.lookup_patient.__wrapped__(args)


def _verified_args(**overrides) -> dict:
    args = {
        "name": "Alice Doe",
        "date_of_birth": "1990-01-01",
        "phone_number": "(555) 123-4567",
        "detail_level": "full",
    }
    args.update(overrides)
    return args


def test_full_patient_payload_contains_complete_verified_details():
    patient = _patient()
    patient.extra.update(
        {
            "recent_procedures": [{"id": "procedure-1"}],
            "insurance_coverages": [{"id": "coverage-1"}],
            "last_visit": {"id": "appointment-previous"},
        }
    )

    payload = handlers._to_full_patient_payload(patient)

    assert payload == {
        "id": "p1",
        "first_name": "Alice",
        "last_name": "Doe",
        "email": "alice@example.com",
        "phone_number": "+15551234567",
        "date_of_birth": "1990-01-01",
        "upcoming_appointments": [{"id": "appt-1"}],
        "last_visit": {"id": "appointment-previous"},
        "recent_procedures": [{"id": "procedure-1"}],
        "insurance_coverages": [{"id": "coverage-1"}],
    }


@pytest.mark.asyncio
async def test_phone_only_returns_no_patient_information_and_skips_pms_search(
    monkeypatch,
):
    ctx = _ctx([_patient()])

    result = await _invoke(
        monkeypatch,
        ctx,
        {"phone_number": "+15551234567", "detail_level": "full"},
    )

    assert result["verification_status"] == "additional_information_required"
    assert result["required_fields"] == ["name", "date_of_birth"]
    assert "patients" not in result
    assert "patient_id" not in result
    assert "count" not in result
    assert "match_status" not in result
    assert "Alice" not in str(result)
    ctx.adapter.search_patients.assert_not_awaited()


@pytest.mark.asyncio
async def test_name_without_dob_or_second_factor_is_neutral_and_skips_search(
    monkeypatch,
):
    ctx = _ctx([_patient()])

    result = await _invoke(monkeypatch, ctx, {"name": "Alice"})

    assert result["verification_status"] == "additional_information_required"
    assert result["required_fields"] == ["date_of_birth", "phone_number_or_email"]
    assert "patients" not in result
    ctx.adapter.search_patients.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_phone_returns_complete_full_details(
    monkeypatch,
):
    ctx = _ctx([_patient()])

    result = await _invoke(monkeypatch, ctx, _verified_args())

    assert result["verification_status"] == "verified"
    assert result["patient_id"] == "p1"
    assert result["patients"] == [
        {
            "id": "p1",
            "first_name": "Alice",
            "last_name": "Doe",
            "email": "alice@example.com",
            "phone_number": "+15551234567",
            "date_of_birth": "1990-01-01",
            "upcoming_appointments": [{"id": "appt-1"}],
        }
    ]


@pytest.mark.asyncio
async def test_verified_email_is_accepted_when_phone_is_not_supplied(monkeypatch):
    ctx = _ctx([_patient()])

    result = await _invoke(
        monkeypatch,
        ctx,
        _verified_args(phone_number=None, email="ALICE@example.com"),
    )

    assert result["verification_status"] == "verified"
    assert result["patient_id"] == "p1"


@pytest.mark.asyncio
async def test_first_name_shape_remains_supported(monkeypatch):
    ctx = _ctx([_patient()])

    result = await _invoke(monkeypatch, ctx, _verified_args(name="Alice"))

    assert result["verification_status"] == "verified"
    assert result["patients"][0]["first_name"] == "Alice"


@pytest.mark.asyncio
async def test_future_split_name_shape_is_supported(monkeypatch):
    ctx = _ctx([_patient()])
    args = _verified_args(name=None, first_name="Alice", last_name="Doe")

    result = await _invoke(monkeypatch, ctx, args)

    assert result["verification_status"] == "verified"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Mallory"},
        {"date_of_birth": "1980-05-05"},
        {"phone_number": "(415) 555-4567"},
    ],
)
async def test_mismatched_claims_return_no_patient_information(monkeypatch, overrides):
    ctx = _ctx([_patient()])

    result = await _invoke(monkeypatch, ctx, _verified_args(**overrides))

    assert result == handlers._verification_failed_response()
    assert "patients" not in result
    assert "patient_id" not in result
    assert "Alice" not in str(result)


@pytest.mark.asyncio
async def test_no_match_failed_match_and_ambiguous_match_are_indistinguishable(
    monkeypatch,
):
    args = _verified_args()
    no_match = await _invoke(monkeypatch, _ctx([]), args)
    failed_match = await _invoke(
        monkeypatch,
        _ctx([_patient(phone="+14155554567")]),
        args,
    )
    ambiguous_match = await _invoke(
        monkeypatch,
        _ctx([_patient(pid="p1"), _patient(pid="p2")]),
        args,
    )

    assert no_match == failed_match == ambiguous_match
    assert no_match == handlers._verification_failed_response()


@pytest.mark.asyncio
async def test_basic_lookup_still_requires_verification_and_returns_only_id(
    monkeypatch,
):
    ctx = _ctx([_patient()])

    result = await _invoke(
        monkeypatch,
        ctx,
        _verified_args(detail_level="basic"),
    )

    assert result["verification_status"] == "verified"
    assert result["detail_level"] == "basic"
    assert result["patients"] == [{"id": "p1"}]
    assert ctx.adapter.search_patients.await_count == 1


@pytest.mark.asyncio
async def test_full_detail_refetch_keeps_only_the_verified_patient(monkeypatch):
    original = _patient(pid="p1")
    refreshed = _patient(pid="p1")
    unrelated = _patient(pid="p2", first_name="Other", last_name="Person")
    ctx = _ctx([original])
    ctx.adapter.search_patients.side_effect = [[original], [unrelated, refreshed]]

    result = await _invoke(monkeypatch, ctx, _verified_args())

    assert result["patient_id"] == "p1"
    assert result["patients"][0]["id"] == "p1"
    assert len(result["patients"]) == 1
