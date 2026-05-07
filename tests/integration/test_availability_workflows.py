"""Integration-style tests for institution availability setup workflows."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.app.api.routes import institution_setup as route
from src.app.pms.base import SupportsAvailabilityLinking


class _FakeSession:
    pass


class _FakeAvailabilityAdapter(SupportsAvailabilityLinking):
    def __init__(self):
        self.created_payload = None

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

    async def update_availability(self, *args, **kwargs):
        raise NotImplementedError

    async def list_availabilities(self, **kwargs):
        return []


def _monkeypatch_route_context(monkeypatch, adapter):
    @asynccontextmanager
    async def fake_db_session():
        yield _FakeSession()

    async def fake_resolve(_current_user, _session, _location_id):
        return (
            SimpleNamespace(id="inst-1"),
            SimpleNamespace(id="loc-1", slug="loc-1"),
        )

    async def fake_get_adapter(*_args, **_kwargs):
        return adapter

    monkeypatch.setattr(route, "get_db_session", lambda: fake_db_session())
    monkeypatch.setattr(route, "_resolve_institution_location", fake_resolve)
    monkeypatch.setattr(route, "_get_adapter", fake_get_adapter)
    monkeypatch.setattr(route, "log_audit_background", lambda **_kwargs: None)


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
        current_user=SimpleNamespace(id="user-1", role="INSTITUTION_ADMIN"),
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
