"""Integration-style tests for institution availability setup workflows."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.app.api.routes import institution_setup as route
from src.app.pms.base import SupportsAvailabilityLinking


class _FakeSession:
    pass


class _FakeAvailabilityAdapter(SupportsAvailabilityLinking):
    def __init__(self):
        self.created_payload = None
        self.updated_payloads = []
        self.availabilities = []
        self.list_calls = 0
        self.fail_ids: set[str] = set()

    async def link_availability(
        self,
        provider_id,
        appointment_type_ids,
        operatory_id,
        days,
        start_time,
        end_time,
    ):
        self.created_payload = {
            "provider_id": provider_id,
            "appointment_type_ids": appointment_type_ids,
            "operatory_id": operatory_id,
            "days": days,
            "start_time": start_time,
            "end_time": end_time,
        }
        return {
            "data": {
                "id": 999,
                "provider_id": 123,
                "operatory_id": 789,
                "begin_time": "09:00",
                "end_time": "17:00",
                "days": ["Monday"],
                "appointment_types": [{"id": 50, "name": "Cleaning"}],
            }
        }

    async def update_availability(self, availability_id, **kwargs):
        if availability_id in self.fail_ids:
            raise RuntimeError("nexhealth rejected the patch")
        self.updated_payloads.append({"availability_id": availability_id, **kwargs})
        return {"id": availability_id, **kwargs}

    async def list_availabilities(self, **kwargs):
        self.list_calls += 1
        self.list_kwargs = kwargs
        return self.availabilities


def _monkeypatch_route_context(monkeypatch, adapter, *, today="2026-08-20"):
    @asynccontextmanager
    async def fake_db_session():
        yield _FakeSession()

    async def fake_resolve(_current_user, _session, _location_id):
        return (
            SimpleNamespace(id="inst-1"),
            SimpleNamespace(id="loc-1", slug="loc-1", timezone="America/Toronto"),
        )

    async def fake_get_adapter(*_args, **_kwargs):
        return adapter

    monkeypatch.setattr(route, "get_db_session", lambda: fake_db_session())
    monkeypatch.setattr(route, "_resolve_institution_location", fake_resolve)
    monkeypatch.setattr(route, "_get_adapter", fake_get_adapter)
    monkeypatch.setattr(route, "log_audit_background", lambda **_kwargs: None)
    monkeypatch.setattr(route, "_today_for_location", lambda _location: today)


def _admin():
    return SimpleNamespace(id="user-1", role="INSTITUTION_ADMIN")


@pytest.mark.asyncio
async def test_create_availability_returns_cached_response_shape(monkeypatch):
    adapter = _FakeAvailabilityAdapter()
    _monkeypatch_route_context(monkeypatch, adapter)

    result = await route.create_availability(
        req=route.CreateAvailabilityRequest(
            provider_id="nh-123",
            appointment_type_ids=["nh-50"],
            operatory_id="nh-789",
            days=["Monday"],
            start_time="09:00",
            end_time="17:00",
        ),
        current_user=_admin(),
        location_id=None,
    )

    assert adapter.created_payload == {
        "provider_id": "nh-123",
        "appointment_type_ids": ["nh-50"],
        "operatory_id": "nh-789",
        "days": ["Monday"],
        "start_time": "09:00",
        "end_time": "17:00",
    }
    assert result.source_id == "nh-999"
    assert result.provider_source_id == "nh-123"
    assert result.appointment_type_ids == ["nh-50"]


def _range_availabilities():
    return [
        {
            "id": 101,
            "provider_id": 2,
            "operatory_id": 4,
            "specific_date": "2026-08-21",
            "days": ["Friday"],
            "active": True,
        },
        {
            "id": 104,
            "provider_id": 2,
            "operatory_id": 9,
            "specific_date": "2026-08-22",
            "days": ["Saturday"],
            "active": True,
        },
        {
            "id": 102,
            "provider_id": 2,
            "operatory_id": 4,
            "specific_date": "2026-09-01",
            "active": True,
        },
        {
            "id": 103,
            "provider_id": 2,
            "operatory_id": 4,
            "days": ["Wednesday"],
            "active": True,
        },
        {
            "id": 105,
            "provider_id": 2,
            "operatory_id": 4,
            "specific_date": "2026-08-21",
            "active": False,
        },
    ]


@pytest.mark.asyncio
async def test_preview_matches_only_selected_dated_windows(monkeypatch):
    adapter = _FakeAvailabilityAdapter()
    adapter.availabilities = _range_availabilities()
    _monkeypatch_route_context(monkeypatch, adapter)

    result = await route.preview_bulk_link_range_availabilities(
        req=route.BulkLinkRangePreviewRequest(
            provider_id="nh-2",
            operatory_ids=["nh-4"],
            start_date="2026-08-20",
            end_date="2026-08-22",
        ),
        current_user=_admin(),
        location_id=None,
    )

    assert adapter.list_kwargs == {"provider_id": "nh-2", "ignore_past_dates": False}
    assert result.day_count == 3
    assert result.matched_count == 1
    assert [window.source_id for window in result.windows] == ["nh-101"]
    assert adapter.updated_payloads == []
    assert result.batch_size == route.BULK_LINK_BATCH_SIZE
    assert result.batch_pause_seconds == route.BULK_LINK_BATCH_PAUSE_SECONDS


@pytest.mark.asyncio
async def test_preview_matches_multiple_selected_operatories(monkeypatch):
    adapter = _FakeAvailabilityAdapter()
    adapter.availabilities = _range_availabilities()
    _monkeypatch_route_context(monkeypatch, adapter)

    result = await route.preview_bulk_link_range_availabilities(
        req=route.BulkLinkRangePreviewRequest(
            provider_id="nh-2",
            operatory_ids=["nh-4", "nh-9"],
            start_date="2026-08-20",
            end_date="2026-08-22",
        ),
        current_user=_admin(),
        location_id=None,
    )

    assert sorted(window.source_id for window in result.windows) == ["nh-101", "nh-104"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start_date", "end_date", "expected"),
    [
        ("2026-08-19", "2026-08-21", "start_date must not be in the past"),
        ("2026-08-22", "2026-08-21", "end_date must not be before start_date"),
        ("2026-08-20", "2026-09-04", "Date range must not exceed 15 days"),
        ("not-a-date", "2026-08-21", "start_date and end_date must be YYYY-MM-DD dates"),
    ],
)
async def test_preview_rejects_invalid_ranges(monkeypatch, start_date, end_date, expected):
    adapter = _FakeAvailabilityAdapter()
    _monkeypatch_route_context(monkeypatch, adapter)

    with pytest.raises(HTTPException) as exc_info:
        await route.preview_bulk_link_range_availabilities(
            req=route.BulkLinkRangePreviewRequest(
                provider_id="nh-2",
                operatory_ids=["nh-4"],
                start_date=start_date,
                end_date=end_date,
            ),
            current_user=_admin(),
            location_id=None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == expected
    assert adapter.list_calls == 0


@pytest.mark.asyncio
async def test_apply_links_one_bounded_batch(monkeypatch):
    adapter = _FakeAvailabilityAdapter()
    _monkeypatch_route_context(monkeypatch, adapter)

    result = await route.apply_bulk_link_range_availabilities(
        req=route.BulkLinkRangeApplyRequest(
            availability_ids=["nh-101", "nh-104"],
            appointment_type_ids=["nh-50", "nh-51"],
        ),
        current_user=_admin(),
        location_id=None,
    )

    assert result.updated_ids == ["nh-101", "nh-104"]
    assert result.updated_count == 2
    assert result.errors == []
    assert adapter.list_calls == 0


@pytest.mark.asyncio
async def test_apply_reports_per_window_failures(monkeypatch):
    adapter = _FakeAvailabilityAdapter()
    adapter.fail_ids = {"nh-104"}
    _monkeypatch_route_context(monkeypatch, adapter)

    result = await route.apply_bulk_link_range_availabilities(
        req=route.BulkLinkRangeApplyRequest(
            availability_ids=["nh-101", "nh-104", "nh-106"],
            appointment_type_ids=["nh-50"],
        ),
        current_user=_admin(),
        location_id=None,
    )

    assert result.updated_ids == ["nh-101", "nh-106"]
    assert result.updated_count == 2
    assert len(result.errors) == 1
    assert result.errors[0].startswith("nh-104: ")


def test_apply_request_rejects_an_empty_or_oversized_batch():
    over_cap = [f"nh-{index}" for index in range(route.BULK_LINK_BATCH_SIZE + 1)]

    with pytest.raises(ValidationError):
        route.BulkLinkRangeApplyRequest(
            availability_ids=over_cap,
            appointment_type_ids=["nh-50"],
        )
    with pytest.raises(ValidationError):
        route.BulkLinkRangeApplyRequest(availability_ids=[], appointment_type_ids=["nh-50"])
    with pytest.raises(ValidationError):
        route.BulkLinkRangeApplyRequest(availability_ids=["nh-101"], appointment_type_ids=[])
