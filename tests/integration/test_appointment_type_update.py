"""Integration tests for updating appointment types in institution_setup routes."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.api.routes import institution_setup as route
from src.app.pms.base import SupportsAppointmentTypeCreation
from src.app.pms.models import UniversalAppointmentType


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, cached):
        self._cached = cached

    async def execute(self, _stmt):
        return _FakeResult(self._cached)

    async def flush(self):
        return None


class _FakeSyncService:
    def __init__(self, _session):
        self.called = False

    async def _upsert_appointment_type(self, **_kwargs):
        self.called = True


class _FakeAdapter(SupportsAppointmentTypeCreation):
    async def list_pms_descriptors(self):
        return []

    async def create_appointment_type(self, name, duration_minutes, descriptor_ids):
        raise NotImplementedError

    async def update_appointment_type(
        self,
        appointment_type_id,
        name=None,
        duration_minutes=None,
        descriptor_ids=None,
    ):
        return UniversalAppointmentType(
            id=appointment_type_id,
            source="nexhealth",
            name=name or "Updated",
            duration_minutes=duration_minutes,
            source_id=appointment_type_id,
            source_metadata={"descriptor_ids": descriptor_ids or []},
        )


def _monkeypatch_session(monkeypatch, cached):
    fake_session = _FakeSession(cached)

    @asynccontextmanager
    async def fake_db_session():
        yield fake_session

    async def fake_resolve(_current_user, _session, _location_id):
        inst = SimpleNamespace(id="inst-1")
        loc = SimpleNamespace(id="loc-1")
        return inst, loc

    monkeypatch.setattr(route, "get_db_session", lambda: fake_db_session())
    monkeypatch.setattr(route, "_resolve_institution_location", fake_resolve)


@pytest.mark.asyncio
async def test_update_appointment_type_success(monkeypatch):
    cached = SimpleNamespace(
        id="appt-1",
        source_id="nh-100",
        name="Updated Name",
        duration_minutes=45,
        source_metadata={"descriptor_ids": ["11", "12"]},
        is_active=True,
        synced_at=datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc),
    )
    _monkeypatch_session(monkeypatch, cached)
    async def fake_get_adapter(*_args, **_kwargs):
        return _FakeAdapter()

    monkeypatch.setattr(route, "_get_adapter", fake_get_adapter)
    monkeypatch.setattr(route, "SyncService", _FakeSyncService)

    req = route.UpdateAppointmentTypeRequest(
        name="Updated Name",
        duration_minutes=45,
        descriptor_ids=["11", "12"],
    )
    result = await route.update_appointment_type(
        source_id="nh-100",
        req=req,
        current_user=SimpleNamespace(),
        location_id=None,
    )

    assert result.name == "Updated Name"
    assert result.duration_minutes == 45


@pytest.mark.asyncio
async def test_update_appointment_type_requires_fields():
    req = route.UpdateAppointmentTypeRequest()
    with pytest.raises(HTTPException) as exc:
        await route.update_appointment_type(
            source_id="nh-100",
            req=req,
            current_user=SimpleNamespace(),
            location_id=None,
        )
    assert exc.value.status_code == 400
    assert "No fields provided" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_appointment_type_requires_capability(monkeypatch):
    cached = SimpleNamespace(
        id="appt-1",
        source_id="nh-100",
        name="Updated Name",
        duration_minutes=45,
        source_metadata=None,
        is_active=True,
    )
    _monkeypatch_session(monkeypatch, cached)
    async def fake_get_adapter(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(route, "_get_adapter", fake_get_adapter)

    req = route.UpdateAppointmentTypeRequest(name="Updated Name")
    with pytest.raises(HTTPException) as exc:
        await route.update_appointment_type(
            source_id="nh-100",
            req=req,
            current_user=SimpleNamespace(),
            location_id=None,
        )
    assert exc.value.status_code == 400
    assert "does not support updating appointment types" in str(exc.value.detail)
