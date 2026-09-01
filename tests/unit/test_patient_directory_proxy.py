"""Live patient directory proxies one bounded PMS page and limits PHI."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.app.api.routes.universal import patients as route
from src.app.pms.models import UniversalPatient, UniversalPatientPage


def _unwrap(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _user(role="LOCATION_ADMIN"):
    return SimpleNamespace(
        id="user-1",
        role=role,
        institution_id="inst-1",
        location_id="loc-1",
    )


def _request():
    request = MagicMock()
    request.state.location = SimpleNamespace(id="loc-1")
    return request


class _PMS:
    source = "nexhealth"

    def __init__(self, page: UniversalPatientPage | None = None, error=None):
        self.page = page
        self.error = error
        self.calls = []
        self.close = AsyncMock()

    async def browse_patients(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        assert self.page is not None
        return self.page


@pytest.mark.asyncio
async def test_location_admin_patient_page_shows_contact_fields_and_links_contact(
    monkeypatch,
):
    pms = _PMS(
        UniversalPatientPage(
            items=[
                UniversalPatient(
                    id="nh-42",
                    source="nexhealth",
                    first_name="Dana",
                    last_name="Reyes",
                    email="dana@example.com",
                    phone="+12125551234",
                    date_of_birth="1988-04-02",
                    extra={
                        "inactive": False,
                        "updated_at": "2026-09-01T10:00:00Z",
                    },
                )
            ],
            total=75,
            next_cursor="next:cursor-1",
            has_next_page=True,
        )
    )
    monkeypatch.setattr(
        route,
        "_local_contact_ids",
        AsyncMock(return_value={"nh-42": "contact-42"}),
    )

    response = await _unwrap(route.browse_patients)(
        request=_request(),
        current_user=_user(),
        cursor=None,
        page_size=25,
        search="Dana",
        patient_status="active",
        reveal_patient_id=None,
        pms=pms,
    )

    assert response.total == 75
    assert response.next_cursor == "next:cursor-1"
    assert response.items[0].email == "dana@example.com"
    assert response.items[0].phone == "+12125551234"
    assert response.items[0].contact_details_masked is False
    assert response.items[0].can_reveal_contact_details is False
    assert response.items[0].contact_id == "contact-42"
    assert "1988-04-02" not in response.model_dump_json()
    assert pms.calls == [
        {
            "cursor": None,
            "page_size": 25,
            "name": "Dana",
            "status": "active",
        }
    ]
    pms.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reveal_patient_id", "expected_email", "masked", "can_reveal"),
    [
        (None, None, True, True),
        ("nh-42", "dana@example.com", False, False),
    ],
)
async def test_institution_admin_masks_until_one_patient_is_revealed(
    monkeypatch,
    reveal_patient_id,
    expected_email,
    masked,
    can_reveal,
):
    pms = _PMS(
        UniversalPatientPage(
            items=[
                UniversalPatient(
                    id="nh-42",
                    source="nexhealth",
                    first_name="Dana",
                    last_name="Reyes",
                    email="dana@example.com",
                    phone="+12125551234",
                )
            ]
        )
    )
    monkeypatch.setattr(route, "_local_contact_ids", AsyncMock(return_value={}))

    response = await _unwrap(route.browse_patients)(
        request=_request(),
        current_user=_user("INSTITUTION_ADMIN"),
        cursor=None,
        page_size=25,
        search=None,
        patient_status="active",
        reveal_patient_id=reveal_patient_id,
        pms=pms,
    )

    item = response.items[0]
    assert item.email == expected_email
    assert item.phone == ("+12125551234" if expected_email else None)
    assert item.email_masked == "d***@example.com"
    assert item.phone_masked.endswith("1234")
    assert item.contact_details_masked is masked
    assert item.can_reveal_contact_details is can_reveal


@pytest.mark.asyncio
async def test_live_patient_page_returns_safe_503_and_closes_adapter():
    pms = _PMS(error=RuntimeError("patient Dana +15551234567 failed"))

    with pytest.raises(HTTPException) as exc:
        await _unwrap(route.browse_patients)(
            request=_request(),
            current_user=_user(),
            cursor=None,
            page_size=25,
            search=None,
            patient_status="active",
            reveal_patient_id=None,
            pms=pms,
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == (
        "The practice system is temporarily unavailable. Try again shortly."
    )
    assert "Dana" not in exc.value.detail
    pms.close.assert_awaited_once()
