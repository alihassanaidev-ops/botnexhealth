from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, time, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.api.routes import institution_setup as route


class _FakeResult:
    def __init__(self, provider):
        self._provider = provider

    def scalar_one_or_none(self):
        return self._provider


class _FakeSession:
    def __init__(self, provider):
        self._provider = provider

    async def execute(self, _stmt):
        return _FakeResult(self._provider)

    async def flush(self):
        return None

    async def refresh(self, _provider):
        return None


def _provider():
    return SimpleNamespace(
        id="prov-1",
        institution_id="inst-1",
        location_id="loc-1",
        source_id="nh-123",
        name="Dr Smith",
        first_name="Dr",
        last_name="Smith",
        specialty="General",
        is_active=True,
        buffer_minutes=15,
        same_day_cutoff_time=time(14, 0),
        synced_at=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_update_provider_null_clears_cutoff(monkeypatch: pytest.MonkeyPatch):
    provider = _provider()
    fake_session = _FakeSession(provider)

    @asynccontextmanager
    async def fake_db_session():
        yield fake_session

    async def fake_resolve(_current_user, _session, _location_id):
        return SimpleNamespace(id="inst-1"), SimpleNamespace(id="loc-1")

    monkeypatch.setattr(route, "get_db_session", lambda: fake_db_session())
    monkeypatch.setattr(route, "_resolve_institution_location", fake_resolve)

    req = route.UpdateProviderRequest(same_day_cutoff_time=None)
    assert "same_day_cutoff_time" in req.model_fields_set

    result = await route.update_provider(
        provider_id=provider.id,
        req=req,
        current_user=SimpleNamespace(),
        location_id=None,
    )

    assert provider.same_day_cutoff_time is None
    assert result.same_day_cutoff_time is None


@pytest.mark.asyncio
async def test_update_provider_rejects_seconds_in_cutoff(monkeypatch: pytest.MonkeyPatch):
    provider = _provider()
    fake_session = _FakeSession(provider)

    @asynccontextmanager
    async def fake_db_session():
        yield fake_session

    async def fake_resolve(_current_user, _session, _location_id):
        return SimpleNamespace(id="inst-1"), SimpleNamespace(id="loc-1")

    monkeypatch.setattr(route, "get_db_session", lambda: fake_db_session())
    monkeypatch.setattr(route, "_resolve_institution_location", fake_resolve)

    req = route.UpdateProviderRequest(same_day_cutoff_time="14:00:30")
    with pytest.raises(HTTPException) as exc:
        await route.update_provider(
            provider_id=provider.id,
            req=req,
            current_user=SimpleNamespace(),
            location_id=None,
        )

    assert exc.value.status_code == 400
    assert "HH:MM format" in str(exc.value.detail)
