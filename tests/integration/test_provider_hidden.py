"""Tests for hiding providers from the Retell ``list_providers`` tool.

NexHealth reports a practice's full provider record, including staff who no
longer see patients. `institution_providers.is_hidden` lets an operator drop
those from what the voice agent offers, per location.

Covers:
1. Retell ``list_providers`` excludes hidden providers, with and without a DOB.
2. Hiding is independent of ``is_active`` (which the PMS sync rewrites).
3. ``PATCH /institution/setup/providers/{id}`` round-trips the flag.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

from src.app.api.routes import institution_setup as route


# ── Helpers ──────────────────────────────────────────────────────────────


def _pms_provider(source_id, name):
    """PMS provider as the adapter returns it — ids are prefixed by the mapper."""
    return SimpleNamespace(
        id=source_id,
        name=name,
        first_name=name.split()[0],
        last_name=name.split()[-1],
        specialty="General",
        appointment_types=[],
        operatory_ids=[],
    )


def _row(source_id, *, is_hidden=False, is_active=True, min_age=None, max_age=None):
    return SimpleNamespace(
        source_id=source_id,
        is_hidden=is_hidden,
        is_active=is_active,
        min_age=min_age,
        max_age=max_age,
    )


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _Rows(self._rows)


def _patch_handler(monkeypatch, providers, rows):
    """Wire the Retell handler to fixed PMS providers and fixed cache rows."""
    from src.app.retell import handlers

    async def mock_list_providers():
        return providers

    async def mock_resolve():
        return SimpleNamespace(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            adapter=SimpleNamespace(list_providers=mock_list_providers),
        )

    @asynccontextmanager
    async def fake_db(*_args, **_kwargs):
        yield _Session(rows)

    monkeypatch.setattr(handlers, "_resolve_context", mock_resolve)
    monkeypatch.setattr(handlers, "get_system_db_session", fake_db)
    return handlers


# ── Retell tool ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hidden_provider_excluded_without_dob(monkeypatch):
    """The filter must not depend on a DOB being supplied."""
    handlers = _patch_handler(
        monkeypatch,
        providers=[
            _pms_provider("nh-100", "Dr Real"),
            _pms_provider("nh-200", "Dr Retired"),
        ],
        rows=[_row("nh-100"), _row("nh-200", is_hidden=True)],
    )

    result = await handlers.list_providers({})

    assert result["count"] == 1
    assert [p["name"] for p in result["providers"]] == ["Dr Real"]


@pytest.mark.asyncio
async def test_hidden_provider_excluded_with_dob(monkeypatch):
    """Hiding wins even when the age rule would have admitted the provider."""
    handlers = _patch_handler(
        monkeypatch,
        providers=[
            _pms_provider("nh-100", "Dr Real"),
            _pms_provider("nh-200", "Dr Retired"),
        ],
        # Both cover every age; only the hidden flag separates them.
        rows=[
            _row("nh-100", min_age=0, max_age=150),
            _row("nh-200", is_hidden=True, min_age=0, max_age=150),
        ],
    )

    dob = date.today().replace(year=date.today().year - 30).isoformat()
    result = await handlers.list_providers({"date_of_birth": dob})

    assert result["count"] == 1
    assert [p["name"] for p in result["providers"]] == ["Dr Real"]


@pytest.mark.asyncio
async def test_hidden_applies_even_when_not_in_last_sync(monkeypatch):
    """is_hidden is operator intent, so a stale is_active must not revive them.

    `is_active` records "seen in the last PMS sync". A hidden provider whose row
    is stale must still stay hidden.
    """
    handlers = _patch_handler(
        monkeypatch,
        providers=[
            _pms_provider("nh-100", "Dr Real"),
            _pms_provider("nh-200", "Dr Retired"),
        ],
        rows=[_row("nh-100"), _row("nh-200", is_hidden=True, is_active=False)],
    )

    result = await handlers.list_providers({})

    assert result["count"] == 1
    assert [p["name"] for p in result["providers"]] == ["Dr Real"]


@pytest.mark.asyncio
async def test_no_hidden_rows_returns_everyone(monkeypatch):
    """Nothing hidden — the tool behaves exactly as before."""
    handlers = _patch_handler(
        monkeypatch,
        providers=[
            _pms_provider("nh-100", "Dr One"),
            _pms_provider("nh-200", "Dr Two"),
        ],
        rows=[_row("nh-100"), _row("nh-200")],
    )

    result = await handlers.list_providers({})

    assert result["count"] == 2


@pytest.mark.asyncio
async def test_provider_absent_from_cache_is_still_listed(monkeypatch):
    """A provider with no local row is included — hiding is opt-in."""
    handlers = _patch_handler(
        monkeypatch,
        providers=[_pms_provider("nh-999", "Dr Brand New")],
        rows=[],
    )

    result = await handlers.list_providers({})

    assert result["count"] == 1


# ── Admin PATCH ──────────────────────────────────────────────────────────


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


def _provider_row(**overrides):
    defaults = dict(
        id="prov-1",
        institution_id="inst-1",
        location_id="loc-1",
        source_id="nh-123",
        name="Dr Smith",
        first_name="Dr",
        last_name="Smith",
        specialty="General",
        is_active=True,
        is_hidden=False,
        buffer_minutes=15,
        same_day_cutoff_time=time(14, 0),
        min_age=None,
        max_age=None,
        synced_at=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_route(monkeypatch, provider):
    fake_session = _FakeSession(provider)

    @asynccontextmanager
    async def fake_db_session():
        yield fake_session

    async def fake_resolve(_current_user, _session, _location_id):
        return (
            SimpleNamespace(id="inst-1"),
            SimpleNamespace(id="loc-1", slug="loc-1"),
        )

    monkeypatch.setattr(route, "get_db_session", lambda: fake_db_session())
    monkeypatch.setattr(route, "_resolve_institution_location", fake_resolve)


@pytest.mark.asyncio
async def test_patch_sets_and_clears_is_hidden(monkeypatch):
    provider = _provider_row()
    _patch_route(monkeypatch, provider)

    response = await route.update_provider(
        provider_id="prov-1",
        req=route.UpdateProviderRequest(is_hidden=True),
        current_user=SimpleNamespace(id="user-1", role="INSTITUTION_ADMIN"),
        location_id="loc-1",
    )
    assert provider.is_hidden is True
    assert response.is_hidden is True

    response = await route.update_provider(
        provider_id="prov-1",
        req=route.UpdateProviderRequest(is_hidden=False),
        current_user=SimpleNamespace(id="user-1", role="INSTITUTION_ADMIN"),
        location_id="loc-1",
    )
    assert provider.is_hidden is False
    assert response.is_hidden is False


@pytest.mark.asyncio
async def test_patch_without_is_hidden_leaves_it_untouched(monkeypatch):
    """Omitting the field must not reset it — the tri-state contract."""
    provider = _provider_row(is_hidden=True)
    _patch_route(monkeypatch, provider)

    await route.update_provider(
        provider_id="prov-1",
        req=route.UpdateProviderRequest(buffer_minutes=30),
        current_user=SimpleNamespace(id="user-1", role="INSTITUTION_ADMIN"),
        location_id="loc-1",
    )

    assert provider.is_hidden is True
    assert provider.buffer_minutes == 30
