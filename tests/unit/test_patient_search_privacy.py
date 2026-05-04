from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from src.app.api.routes.universal import patients as patient_routes
from src.app.pms.models import UniversalPatient


def _unwrap(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _user():
    return SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        role="STAFF",
        institution_id="11111111-1111-1111-1111-111111111111",
        location_id="22222222-2222-2222-2222-222222222222",
    )


class _FakePMS:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def search_patients(self, query: str, **kwargs: Any) -> list[UniversalPatient]:
        self.calls.append((query, kwargs))
        return [
            UniversalPatient(
                id="nh-1",
                source="nexhealth",
                first_name="Jane",
                last_name="Smith",
                email="jane@example.test",
                phone="+15551234567",
                date_of_birth="1985-01-02",
            )
        ]


@pytest.mark.asyncio
async def test_patient_search_rejects_empty_enumeration_request() -> None:
    pms = _FakePMS()

    with pytest.raises(HTTPException) as exc:
        await _unwrap(patient_routes.search_patients)(
            request=object(),
            current_user=_user(),
            q="",
            email=None,
            phone_number=None,
            date_of_birth=None,
            pms=pms,
        )

    assert exc.value.status_code == 400
    assert pms.calls == []


@pytest.mark.asyncio
async def test_patient_search_rejects_single_character_typeahead_without_identifier() -> None:
    pms = _FakePMS()

    with pytest.raises(HTTPException) as exc:
        await _unwrap(patient_routes.search_patients)(
            request=object(),
            current_user=_user(),
            q="j",
            email=None,
            phone_number=None,
            date_of_birth=None,
            pms=pms,
        )

    assert exc.value.status_code == 400
    assert pms.calls == []


@pytest.mark.asyncio
async def test_patient_search_allows_exact_identifier_with_short_query() -> None:
    pms = _FakePMS()

    patients = await _unwrap(patient_routes.search_patients)(
        request=object(),
        current_user=_user(),
        q="",
        email="jane@example.test",
        phone_number=None,
        date_of_birth=None,
        pms=pms,
    )

    assert len(patients) == 1
    assert pms.calls == [
        (
            "",
            {
                "email": "jane@example.test",
                "phone_number": None,
                "date_of_birth": None,
            },
        )
    ]
