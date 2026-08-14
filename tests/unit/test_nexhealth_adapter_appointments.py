from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.pms.nexhealth import adapter as adapter_module
from src.app.pms.nexhealth.adapter import NexHealthAdapter


def _make_adapter(api_contract: str = "legacy_v2") -> NexHealthAdapter:
    return NexHealthAdapter(
        client=SimpleNamespace(),
        institution=SimpleNamespace(),
        subdomain="test-subdomain",
        location_id="test-location",
        api_contract=api_contract,
    )


@pytest.mark.asyncio
async def test_create_reuses_shared_nexhealth_client(monkeypatch: pytest.MonkeyPatch):
    from src.app import dependencies
    from src.app.config import settings as global_settings

    class SharedClient:
        close_calls = 0

        async def __aexit__(self, exc_type, exc, tb) -> None:
            self.close_calls += 1

    shared_client = SharedClient()

    async def fake_dependency():
        return shared_client

    monkeypatch.setattr(global_settings, "nexhealth_api_key", "test-api-key")
    monkeypatch.setattr(dependencies, "get_nexhealth_client_dependency", fake_dependency)

    adapter = await NexHealthAdapter.create(
        SimpleNamespace(),
        SimpleNamespace(
            slug="test-location",
            nexhealth_subdomain="test-subdomain",
            nexhealth_location_id="123",
        ),
    )

    assert adapter._client is shared_client

    await adapter.close()

    assert shared_client.close_calls == 0


@pytest.mark.asyncio
async def test_list_patients_uses_updated_since_and_location_params(monkeypatch: pytest.MonkeyPatch):
    adapter = _make_adapter()
    calls: list[dict] = []

    async def fake_request(client, method, path, params=None, json=None):
        calls.append(
            {
                "client": client,
                "method": method,
                "path": path,
                "params": params,
                "json": json,
            }
        )
        return {"data": {"patients": [{"id": "pat-1"}]}, "count": 1}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    patients = await adapter.list_patients(updated_since="2026-07-21T09:00:00Z")

    assert patients == [{"id": "pat-1"}]
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "/patients"
    assert calls[0]["params"] == {
        "subdomain": "test-subdomain",
        "location_id": "test-location",
        "updated_since": "2026-07-21T09:00:00Z",
        "page": 1,
        "per_page": 50,
    }


@pytest.mark.asyncio
async def test_search_patients_accepts_stable_v3_flat_data(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _make_adapter(api_contract="stable_v3")
    calls: list[dict] = []

    async def fake_request(client, method, path, params=None, json=None):
        calls.append(params or {})
        return {
            "data": [
                {
                    "id": "pat-1",
                    "first_name": "Sam",
                    "last_name": "Lee",
                    "bio": {"date_of_birth": "1990-01-01"},
                }
            ],
            "page_info": {"has_next_page": False, "end_cursor": "cursor-1"},
        }

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    patients = await adapter.search_patients("Sam Lee")

    assert [patient.id for patient in patients] == ["nh-pat-1"]
    assert calls[0] == {
        "subdomain": "test-subdomain",
        "location_id": "test-location",
        "name": "Sam Lee",
        "per_page": 10,
    }


@pytest.mark.asyncio
async def test_list_patients_uses_cursor_pagination_for_stable_v3(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _make_adapter(api_contract="stable_v3")
    calls: list[dict] = []

    async def fake_request(client, method, path, params=None, json=None):
        calls.append(params or {})
        if "end_cursor" not in params:
            return {
                "data": [{"id": "pat-1"}],
                "page_info": {"has_next_page": True, "end_cursor": "cursor-1"},
            }
        return {
            "data": [{"id": "pat-2"}],
            "page_info": {"has_next_page": False, "end_cursor": "cursor-2"},
        }

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    patients = await adapter.list_patients(updated_since="2026-07-21T09:00:00Z")

    assert patients == [{"id": "pat-1"}, {"id": "pat-2"}]
    assert calls == [
        {
            "subdomain": "test-subdomain",
            "location_id": "test-location",
            "updated_since": "2026-07-21T09:00:00Z",
            "per_page": 50,
        },
        {
            "subdomain": "test-subdomain",
            "location_id": "test-location",
            "updated_since": "2026-07-21T09:00:00Z",
            "per_page": 50,
            "end_cursor": "cursor-1",
        },
    ]


@pytest.mark.asyncio
async def test_has_provider_appointments_scans_multiple_pages(monkeypatch: pytest.MonkeyPatch):
    adapter = _make_adapter()
    calls: list[dict] = []

    async def fake_request(client, method, path, params=None, json=None):
        calls.append(params or {})
        if params["page"] == 1:
            return {
                "data": [{"id": i, "cancelled": True} for i in range(50)],
            }
        if params["page"] == 2:
            return {"data": [{"id": 999, "cancelled": False}]}
        return {"data": []}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.has_provider_appointments_on_date("nh-123", "2026-03-09")

    assert result is True
    assert len(calls) == 2
    assert calls[0]["provider_id"] == "123"


@pytest.mark.asyncio
async def test_has_provider_appointments_scans_cursor_pages_for_stable_v3(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _make_adapter(api_contract="stable_v3")
    calls: list[dict] = []

    async def fake_request(client, method, path, params=None, json=None):
        calls.append(params or {})
        if "end_cursor" not in params:
            return {
                "data": [{"id": i, "cancelled": True} for i in range(50)],
                "page_info": {"has_next_page": True, "end_cursor": "cursor-1"},
            }
        return {
            "data": [{"id": 999, "cancelled": False}],
            "page_info": {"has_next_page": False, "end_cursor": "cursor-2"},
        }

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.has_provider_appointments_on_date("nh-123", "2026-03-09")

    assert result is True
    assert calls[0]["provider_id"] == "123"
    assert "page" not in calls[0]
    assert calls[1]["end_cursor"] == "cursor-1"


@pytest.mark.asyncio
async def test_has_provider_appointments_returns_false_when_all_cancelled(monkeypatch: pytest.MonkeyPatch):
    adapter = _make_adapter()

    async def fake_request(client, method, path, params=None, json=None):
        return {"data": [{"id": 1, "cancelled": True}]}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.has_provider_appointments_on_date("nh-123", "2026-03-09")
    assert result is False


@pytest.mark.asyncio
async def test_has_provider_appointments_safe_fallback_on_unexpected_payload(monkeypatch: pytest.MonkeyPatch):
    adapter = _make_adapter()

    async def fake_request(client, method, path, params=None, json=None):
        return {"data": {"appointments": []}}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.has_provider_appointments_on_date("nh-123", "2026-03-09")
    assert result is True


@pytest.mark.asyncio
async def test_list_appointments_paginates_date_window(monkeypatch: pytest.MonkeyPatch):
    adapter = _make_adapter()
    calls: list[dict] = []

    async def fake_request(client, method, path, params=None, json=None):
        calls.append(params or {})
        if params["page"] == 1:
            return {"count": 51, "data": [{"id": i} for i in range(1, 51)]}
        return {"count": 51, "data": [{"id": 51}]}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.list_appointments(
        start_date="2026-08-01",
        end_date="2026-08-31",
    )

    assert [row["id"] for row in result] == list(range(1, 52))
    assert calls[0]["subdomain"] == "test-subdomain"
    assert calls[0]["location_id"] == "test-location"
    # NexHealth /appointments expects `start`/`end` query params (verified live against
    # the sandbox — start_date/end_date returns HTTP 400 "Missing parameter start").
    assert calls[0]["start"] == "2026-08-01"
    assert calls[0]["end"] == "2026-08-31"
    assert calls[1]["page"] == 2


@pytest.mark.asyncio
async def test_list_appointments_uses_cursor_pagination_for_stable_v3(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _make_adapter(api_contract="stable_v3")
    calls: list[dict] = []

    async def fake_request(client, method, path, params=None, json=None):
        calls.append(params or {})
        if "end_cursor" not in params:
            return {
                "data": [{"id": "appt-1"}],
                "page_info": {"has_next_page": True, "end_cursor": "cursor-1"},
            }
        return {
            "data": [{"id": "appt-2"}],
            "page_info": {"has_next_page": False, "end_cursor": "cursor-2"},
        }

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.list_appointments(
        start_date="2026-08-01",
        end_date="2026-08-31",
    )

    assert result == [{"id": "appt-1"}, {"id": "appt-2"}]
    assert calls[0]["start"] == "2026-08-01"
    assert calls[0]["end"] == "2026-08-31"
    assert "page" not in calls[0]
    assert calls[1]["end_cursor"] == "cursor-1"


@pytest.mark.asyncio
async def test_list_availabilities_uses_provider_embedded_windows_when_endpoint_is_empty(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _make_adapter()
    calls: list[tuple[str, dict]] = []

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        calls.append((path, params or {}))
        if path == "/availabilities":
            return {"data": []}
        if path == "/providers":
            return {
                "count": 1,
                "data": [
                    {
                        "id": 123,
                        "first_name": "Ada",
                        "last_name": "Lovelace",
                        "availabilities": [
                            {
                                "id": 456,
                                "provider_id": 123,
                                "operatory_id": 789,
                                "begin_time": "08:00",
                                "end_time": "17:00",
                                "days": ["Monday"],
                                "specific_date": "2099-01-05",
                                "active": True,
                                "appointment_types": [{"id": 50, "name": "Cleaning"}],
                            }
                        ],
                    }
                ],
            }
        return {"data": []}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.list_availabilities(provider_id="nh-123")

    assert len(result) == 1
    assert result[0]["id"] == 456
    assert result[0]["provider_id"] == 123
    assert result[0]["provider_name"] == "Ada Lovelace"
    assert calls[0][0] == "/availabilities"
    assert calls[0][1]["provider_id"] == "123"
    assert calls[1][0] == "/providers"


@pytest.mark.asyncio
async def test_list_availabilities_routes_to_working_hours_for_v3(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _make_adapter(api_contract="stable_v3")
    calls: list[str] = []

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        calls.append(path)
        return {"data": [], "count": 0}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.list_availabilities(provider_id="nh-123")

    assert result == []
    assert calls[0] == "/working_hours"
    assert calls[1] == "/providers"


@pytest.mark.asyncio
async def test_create_availability_wraps_body_under_availability_key(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = params
        captured["json"] = json
        return {"data": {"id": 1}}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    adapter = _make_adapter()
    await adapter.link_availability(
        provider_id="nh-123",
        appointment_type_ids=["nh-50", "51"],
        operatory_id="nh-789",
        days=["Monday"],
        start_time="09:00",
        end_time="17:00",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/availabilities"
    assert captured["json"] == {
        "availability": {
            "provider_id": "123",
            "appointment_type_ids": ["50", "51"],
            "operatory_id": "789",
            "days": ["Monday"],
            "begin_time": "09:00",
            "end_time": "17:00",
        }
    }


@pytest.mark.asyncio
async def test_create_availability_routes_to_working_hours_for_v3(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json
        return {"data": {"id": 1}}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    adapter = _make_adapter(api_contract="stable_v3")
    await adapter.link_availability(
        provider_id="nh-123",
        appointment_type_ids=["nh-50"],
        operatory_id="nh-789",
        days=["Monday"],
        start_time="09:00",
        end_time="17:00",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/working_hours"
    assert captured["json"] == {
        "working_hour": {
            "provider_id": "123",
            "appointment_type_ids": ["50"],
            "operatory_id": "789",
            "days": ["Monday"],
            "begin_time": "09:00",
            "end_time": "17:00",
        }
    }


@pytest.mark.asyncio
async def test_update_availability_routes_to_working_hours_for_v3(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json
        return {"data": {"id": 1, "active": False}}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    adapter = _make_adapter(api_contract="stable_v3")
    await adapter.update_availability("nh-456", active=False)

    assert captured["method"] == "PATCH"
    assert captured["path"] == "/working_hours/456"
    assert captured["json"] == {"working_hour": {"active": False}}


# ── Slots: next_available_date hint ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_available_slots_surfaces_next_available_date(
    monkeypatch: pytest.MonkeyPatch,
):
    """When a provider group is empty, NexHealth's next_available_date must be
    surfaced (per-provider + as the earliest) instead of silently dropped."""
    adapter = _make_adapter()

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        return {
            "data": [
                {"lid": 1, "pid": 123, "slots": [], "next_available_date": "2026-08-01"}
            ]
        }

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.find_available_slots(
        start_date="2026-07-20", days=1, provider_id="nh-123", appointment_type_id="nh-50"
    )

    assert result.slots == []
    assert result.next_available_date == "2026-08-01"
    assert result.next_available_by_provider == {"nh-123": "2026-08-01"}


@pytest.mark.asyncio
async def test_find_available_slots_routes_to_available_slots_for_v3(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _make_adapter(api_contract="stable_v3")
    captured: dict = {}

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = params
        return {"data": [{"lid": 1, "pid": 123, "slots": [], "next_available_date": None}]}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    await adapter.find_available_slots(
        start_date="2026-07-20",
        days=1,
        provider_id="nh-123",
        appointment_type_id="nh-50",
    )

    assert captured["method"] == "GET"
    assert captured["path"] == "/available_slots"
    assert captured["params"]["pids[]"] == ["123"]


@pytest.mark.asyncio
async def test_find_available_slots_returns_earliest_across_providers(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _make_adapter()

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        return {
            "data": [
                {"lid": 1, "pid": 123, "slots": [], "next_available_date": "2026-09-15"},
                {"lid": 1, "pid": 456, "slots": [], "next_available_date": "2026-08-03"},
            ]
        }

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.find_available_slots(
        start_date="2026-07-20", days=1, provider_id=["nh-123", "nh-456"], appointment_type_id="nh-50"
    )

    assert result.next_available_date == "2026-08-03"
    assert result.next_available_by_provider == {
        "nh-123": "2026-09-15",
        "nh-456": "2026-08-03",
    }


@pytest.mark.asyncio
async def test_find_available_slots_none_when_no_availability_in_window(
    monkeypatch: pytest.MonkeyPatch,
):
    """next_available_date null (no openings within lookahead) → None, not error."""
    adapter = _make_adapter()

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        return {"data": [{"lid": 1, "pid": 123, "slots": [], "next_available_date": None}]}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.find_available_slots(
        start_date="2026-07-20", days=1, provider_id="nh-123", appointment_type_id="nh-50"
    )

    assert result.slots == []
    assert result.next_available_date is None
    assert result.next_available_by_provider == {}


@pytest.mark.asyncio
async def test_get_available_slots_still_returns_plain_list(
    monkeypatch: pytest.MonkeyPatch,
):
    """Back-compat: get_available_slots keeps returning a flat UniversalSlot list."""
    from src.app.pms.models import UniversalSlot

    adapter = _make_adapter()

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        return {
            "data": [
                {
                    "lid": 1,
                    "pid": 123,
                    "slots": [
                        {"time": "2026-07-20T09:00:00-04:00", "end_time": "2026-07-20T09:30:00-04:00"}
                    ],
                    "next_available_date": None,
                }
            ]
        }

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    slots = await adapter.get_available_slots(
        start_date="2026-07-20", days=1, provider_id="nh-123", appointment_type_id="nh-50"
    )

    assert isinstance(slots, list)
    assert len(slots) == 1
    assert isinstance(slots[0], UniversalSlot)
    assert slots[0].provider_id == "nh-123"


# ── Reschedule ordering: book new before cancelling old ─────────────────────


def _booking_request() -> "BookingRequest":  # noqa: F821
    from src.app.pms.models import BookingRequest

    return BookingRequest(
        patient_id="patient-1",
        provider_id="provider-1",
        slot_start="2026-05-04T09:00:00Z",
        slot_end="2026-05-04T09:30:00Z",
        appointment_type_id="type-1",
    )


@pytest.mark.asyncio
async def test_reschedule_does_not_cancel_when_new_booking_fails(monkeypatch: pytest.MonkeyPatch):
    """If the new slot cannot be booked, the existing appointment must be left intact."""
    from unittest.mock import AsyncMock
    from src.app.pms.models import BookingResult

    adapter = _make_adapter()
    book_mock = AsyncMock(return_value=BookingResult(success=False, source="nexhealth", status="error", error="slot full"))
    cancel_mock = AsyncMock()
    monkeypatch.setattr(adapter, "book_appointment", book_mock)
    monkeypatch.setattr(adapter, "cancel_appointment", cancel_mock)

    result = await adapter.reschedule_appointment("old-1", _booking_request())

    assert result.success is False
    book_mock.assert_awaited_once()
    cancel_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reschedule_books_new_then_cancels_old_on_success(monkeypatch: pytest.MonkeyPatch):
    from src.app.pms.models import BookingResult

    adapter = _make_adapter()
    call_order: list[str] = []

    async def fake_book(_req):
        call_order.append("book")
        return BookingResult(success=True, source="nexhealth", status="booked", appointment_id="new-1")

    async def fake_cancel(_id):
        call_order.append("cancel")
        return BookingResult(success=True, source="nexhealth", status="cancelled")

    monkeypatch.setattr(adapter, "book_appointment", fake_book)
    monkeypatch.setattr(adapter, "cancel_appointment", fake_cancel)

    result = await adapter.reschedule_appointment("old-1", _booking_request())

    assert result.success is True
    assert call_order == ["book", "cancel"]
    assert "new booked, old cancelled" in (result.message or "")


@pytest.mark.asyncio
async def test_reschedule_returns_warning_when_cancel_fails_after_new_booked(monkeypatch: pytest.MonkeyPatch):
    """New slot is booked but cancel fails — we must surface the manual cleanup warning, not a clean success."""
    from unittest.mock import AsyncMock
    from src.app.pms.models import BookingResult

    adapter = _make_adapter()
    monkeypatch.setattr(
        adapter,
        "book_appointment",
        AsyncMock(return_value=BookingResult(success=True, source="nexhealth", status="booked", appointment_id="new-1")),
    )
    monkeypatch.setattr(
        adapter,
        "cancel_appointment",
        AsyncMock(return_value=BookingResult(success=False, source="nexhealth", status="error", error="appointment locked")),
    )

    result = await adapter.reschedule_appointment("old-1", _booking_request())

    assert result.success is True  # the booking did happen
    assert "failed to cancel old appointment" in (result.message or "").lower()
    assert "please cancel manually" in (result.message or "").lower()


@pytest.mark.asyncio
async def test_confirm_appointment_patches_confirmed_true(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = params
        captured["json"] = json
        return {"data": {"id": 1}}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    adapter = _make_adapter()
    result = await adapter.confirm_appointment("nh-appt-123")

    assert result.success is True
    assert result.status == "confirmed"
    assert captured["method"] == "PATCH"
    assert captured["path"] == "/appointments/appt-123"
    assert captured["json"] == {"appt": {"confirmed": True}}


# ---------------------------------------------------------------------------
# Regression — POST /appointment_types body wrap (NexHealth-specific).
# NexHealth's REST convention requires write payloads wrapped under the
# singular resource name. A flat body returns 400 "Missing parameter
# appointment_type". Reproduced live + verified against staging on
# 2026-05-08; this test pins the wrap so the bug cannot regress silently.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_appointment_type_wraps_body_under_appointment_type_key(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = params
        captured["json"] = json
        return {"data": {"id": 1, "name": "X", "minutes": 30}}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    adapter = _make_adapter()
    await adapter.create_appointment_type(
        name="Hygiene",
        duration_minutes=45,
        descriptor_ids=["nh-12", "34"],
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/appointment_types"
    # The whole point of this test: the body must be wrapped, not flat.
    assert captured["json"] == {
        "appointment_type": {
            "name": "Hygiene",
            "minutes": 45,
            "appointment_descriptor_ids": ["12", "34"],
        }
    }, (
        "create_appointment_type body must be wrapped under "
        "'appointment_type'; flat payloads get 400 'Missing parameter "
        "appointment_type' from NexHealth"
    )
