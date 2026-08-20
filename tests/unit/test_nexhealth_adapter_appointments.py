from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.app.pms.nexhealth import adapter as adapter_module
from src.app.pms.nexhealth.adapter import NexHealthAdapter


@pytest.fixture
def legacy_v2_working_hours(monkeypatch: pytest.MonkeyPatch):
    """Pin the work-window route to v2.

    The route defaults to v3 now, so tests that exercise the legacy
    /availabilities + /providers-embedded merge must say so explicitly.
    """
    from src.app.config import settings as global_settings

    monkeypatch.setattr(
        global_settings, "nexhealth_working_hours_api_version", "v2", raising=False
    )
    return global_settings


def _make_adapter() -> NexHealthAdapter:
    return NexHealthAdapter(
        client=SimpleNamespace(),
        institution=SimpleNamespace(),
        subdomain="test-subdomain",
        location_id="test-location",
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
async def test_list_availabilities_uses_provider_embedded_windows_when_endpoint_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    legacy_v2_working_hours,
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
async def test_list_availabilities_drops_past_dated_embedded_windows(
    monkeypatch: pytest.MonkeyPatch,
    legacy_v2_working_hours,
):
    """Past work windows must never reach the caller.

    NexHealth pre-expands availabilities into one row per date, so a practice's
    history dominates this payload. `ignore_past_dates` used to default to
    False, which left this embedded path unfiltered while the direct
    /availabilities call hardcoded True — the two sources disagreed and the
    permissive one was the expensive one.
    """
    adapter = _make_adapter()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    def window(window_id: int, specific_date: str | None) -> dict:
        return {
            "id": window_id,
            "provider_id": 123,
            "operatory_id": 789,
            "begin_time": "08:00",
            "end_time": "17:00",
            "days": ["Monday"],
            "specific_date": specific_date,
            "active": True,
        }

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        if path == "/providers":
            return {
                "count": 1,
                "data": [
                    {
                        "id": 123,
                        "first_name": "Ada",
                        "last_name": "Lovelace",
                        "availabilities": [
                            window(1, yesterday),
                            window(2, tomorrow),
                            window(3, None),  # recurring rule — no date to expire
                        ],
                    }
                ],
            }
        return {"data": []}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.list_availabilities(provider_id="nh-123")

    assert sorted(item["id"] for item in result) == [2, 3]


@pytest.mark.asyncio
async def test_list_availabilities_past_dates_can_still_be_requested_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    legacy_v2_working_hours,
):
    """The default flipped, but an explicit caller override still wins."""
    adapter = _make_adapter()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    async def fake_request(_client, method, path, *, params=None, json=None, **_kw):
        if path == "/providers":
            return {
                "count": 1,
                "data": [
                    {
                        "id": 123,
                        "availabilities": [
                            {
                                "id": 1,
                                "provider_id": 123,
                                "begin_time": "08:00",
                                "end_time": "17:00",
                                "specific_date": yesterday,
                                "active": True,
                            }
                        ],
                    }
                ],
            }
        return {"data": []}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    result = await adapter.list_availabilities(provider_id="nh-123", ignore_past_dates=False)

    assert [item["id"] for item in result] == [1]


@pytest.mark.asyncio
async def test_list_availabilities_uses_a_longer_budget_and_no_retries(
    monkeypatch: pytest.MonkeyPatch,
    legacy_v2_working_hours,
):
    """This is the slowest read we make, and retrying a timeout multiplies it.

    httpx.TimeoutException subclasses httpx.RequestError, which the HTTP client
    retries — so the default 3 retries turn one slow call into four.
    """
    adapter = _make_adapter()
    seen: list[dict] = []

    async def fake_request(_client, method, path, *, params=None, json=None, **kwargs):
        seen.append({"path": path, **kwargs})
        return {"data": []}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    await adapter.list_availabilities(provider_id="nh-123")

    assert seen, "expected at least one upstream call"
    for call in seen:
        assert call["timeout"] == 60.0, call
        assert call["max_retries"] == 0, call


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


@pytest.fixture
def v3_working_hours(monkeypatch: pytest.MonkeyPatch):
    """Pin the work-window route to v3 (the default, made explicit)."""
    from src.app.config import settings as global_settings

    monkeypatch.setattr(
        global_settings, "nexhealth_working_hours_api_version", "v3.0.0", raising=False
    )
    return global_settings


@pytest.mark.asyncio
async def test_working_hours_v3_uses_the_renamed_route_and_v3_headers(
    monkeypatch: pytest.MonkeyPatch,
    v3_working_hours,
):
    """v3 renamed /availabilities to /working_hours and changed both headers.

    The override is per request: every other route must stay on the
    client-wide contract until the full migration lands.
    """
    adapter = _make_adapter()
    seen: list[dict] = []

    async def fake_request(_client, method, path, *, params=None, json=None, **kwargs):
        seen.append({"path": path, "params": params or {}, **kwargs})
        return {"data": [], "page_info": {"has_next_page": False}}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    await adapter.list_availabilities(provider_id="nh-123")

    assert len(seen) == 1, "v3 needs one paginated route, not the v2 two-path merge"
    call = seen[0]
    assert call["path"] == "/working_hours"
    assert call["headers_override"]["Nex-Api-Version"] == "v3.0.0"
    assert call["headers_override"]["Accept"] == "application/json"
    # Server-side past filtering is the whole point — v2 could not do this.
    assert call["params"]["ignore_past_dates"] == "true"
    assert call["params"]["provider_id"] == "123"


@pytest.mark.asyncio
async def test_working_hours_v3_requests_appointment_types(
    monkeypatch: pytest.MonkeyPatch,
    v3_working_hours,
):
    """v3 omits appointment_types unless asked, and two things depend on them.

    Retell's _validate_appointment_type_for_provider builds its allowed set from
    this field: an empty set rejects every booking. The setup UI also reports
    every window as unlinked without it.
    """
    adapter = _make_adapter()
    seen: list[dict] = []

    async def fake_request(_client, method, path, *, params=None, json=None, **kwargs):
        seen.append(params or {})
        return {"data": [], "page_info": {"has_next_page": False}}

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    await adapter.list_availabilities(provider_id="nh-123")

    assert seen[0]["include[]"] == ["appointment_types"]


@pytest.mark.asyncio
async def test_working_hours_v3_carries_the_label_through(
    monkeypatch: pytest.MonkeyPatch,
    v3_working_hours,
):
    """`label` is the only thing that separates a real window from a note.

    A NexHealth clinic returns Lunch blocks and synced OpenDental notes in the
    same collection as genuine working hours; on v2 they are indistinguishable.
    """
    adapter = _make_adapter()

    async def fake_request(_client, method, path, *, params=None, json=None, **kwargs):
        return {
            "data": [
                {"id": 1, "begin_time": "09:00", "end_time": "13:00",
                 "source": "synced", "label": None},
                {"id": 2, "begin_time": "13:00", "end_time": "14:00",
                 "source": "synced", "label": {"id": 7, "name": "Lunch"}},
                {"id": 3, "begin_time": "10:30", "end_time": "11:10",
                 "source": "synced", "label": {"id": 8, "name": "NOTE"}},
            ],
            "page_info": {"has_next_page": False},
        }

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    rows = await adapter.list_availabilities(provider_id="nh-123")

    by_id = {r["id"]: r for r in rows}
    assert by_id[1]["label_name"] is None
    assert by_id[2]["label_name"] == "Lunch"
    assert by_id[3]["label_name"] == "NOTE"
    # v3 replaced `synced` with `source`; downstream still reads `synced`.
    assert by_id[1]["synced"] is True


@pytest.mark.asyncio
async def test_working_hours_v3_follows_the_cursor(
    monkeypatch: pytest.MonkeyPatch,
    v3_working_hours,
):
    """v3 paginates by opaque cursor, not page number.

    The clinic's busiest provider is ~2k rows at 100/page, so stopping after
    page one would silently truncate most of the schedule.
    """
    adapter = _make_adapter()
    cursors: list[str | None] = []

    async def fake_request(_client, method, path, *, params=None, json=None, **kwargs):
        params = params or {}
        cursors.append(params.get("end_cursor"))
        page = len(cursors)
        if page < 3:
            return {
                "data": [{"id": page, "begin_time": "09:00", "end_time": "17:00"}],
                "page_info": {"has_next_page": True, "end_cursor": f"cur{page}"},
            }
        return {
            "data": [{"id": page, "begin_time": "09:00", "end_time": "17:00"}],
            "page_info": {"has_next_page": False},
        }

    monkeypatch.setattr(adapter_module, "handle_nexhealth_request", fake_request)

    rows = await adapter.list_availabilities()

    assert [r["id"] for r in rows] == [1, 2, 3]
    assert cursors == [None, "cur1", "cur2"]
