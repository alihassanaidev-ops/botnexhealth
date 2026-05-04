"""Integration tests for provider age-group feature.

Tests cover:
1. PATCH /institution/setup/providers/{id} — setting, clearing, and
   validating min_age / max_age.
2. Retell list_providers handler — filtering providers by patient DOB.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.api.routes import institution_setup as route


# ── Helpers ──────────────────────────────────────────────────────────────


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


def _provider(**overrides):
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
        buffer_minutes=15,
        same_day_cutoff_time=time(14, 0),
        min_age=None,
        max_age=None,
        synced_at=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _monkeypatch_session(monkeypatch, provider):
    fake_session = _FakeSession(provider)

    @asynccontextmanager
    async def fake_db_session():
        yield fake_session

    async def fake_resolve(_current_user, _session, _location_id):
        return SimpleNamespace(id="inst-1"), SimpleNamespace(id="loc-1")

    monkeypatch.setattr(route, "get_db_session", lambda: fake_db_session())
    monkeypatch.setattr(route, "_resolve_institution_location", fake_resolve)


# ═══════════════════════════════════════════════════════════════════════════
# 1. PATCH provider — age-group fields
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_provider_set_age_range(monkeypatch):
    """Setting min_age and max_age persists correctly."""
    provider = _provider()
    _monkeypatch_session(monkeypatch, provider)

    req = route.UpdateProviderRequest(min_age=5, max_age=17)
    result = await route.update_provider(
        provider_id=provider.id,
        req=req,
        current_user=SimpleNamespace(),
        location_id=None,
    )

    assert provider.min_age == 5
    assert provider.max_age == 17
    assert result.min_age == 5
    assert result.max_age == 17


@pytest.mark.asyncio
async def test_update_provider_clear_age_range(monkeypatch):
    """Setting age fields to None clears them."""
    provider = _provider(min_age=0, max_age=12)
    _monkeypatch_session(monkeypatch, provider)

    req = route.UpdateProviderRequest(min_age=None, max_age=None)
    result = await route.update_provider(
        provider_id=provider.id,
        req=req,
        current_user=SimpleNamespace(),
        location_id=None,
    )

    assert provider.min_age is None
    assert provider.max_age is None
    assert result.min_age is None
    assert result.max_age is None


@pytest.mark.asyncio
async def test_update_provider_min_age_only(monkeypatch):
    """Setting only min_age is allowed (no upper bound)."""
    provider = _provider()
    _monkeypatch_session(monkeypatch, provider)

    req = route.UpdateProviderRequest(min_age=18)
    result = await route.update_provider(
        provider_id=provider.id,
        req=req,
        current_user=SimpleNamespace(),
        location_id=None,
    )

    assert provider.min_age == 18
    assert provider.max_age is None
    assert result.min_age == 18


@pytest.mark.asyncio
async def test_update_provider_max_age_only(monkeypatch):
    """Setting only max_age is allowed (no lower bound)."""
    provider = _provider()
    _monkeypatch_session(monkeypatch, provider)

    req = route.UpdateProviderRequest(max_age=12)
    result = await route.update_provider(
        provider_id=provider.id,
        req=req,
        current_user=SimpleNamespace(),
        location_id=None,
    )

    assert provider.max_age == 12
    assert provider.min_age is None
    assert result.max_age == 12


@pytest.mark.asyncio
async def test_update_provider_rejects_min_greater_than_max(monkeypatch):
    """min_age > max_age should return 400."""
    provider = _provider()
    _monkeypatch_session(monkeypatch, provider)

    req = route.UpdateProviderRequest(min_age=20, max_age=5)
    with pytest.raises(HTTPException) as exc:
        await route.update_provider(
            provider_id=provider.id,
            req=req,
            current_user=SimpleNamespace(),
            location_id=None,
        )

    assert exc.value.status_code == 400
    assert "min_age cannot be greater than max_age" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_provider_rejects_negative_age(monkeypatch):
    """Negative age values should return 400."""
    provider = _provider()
    _monkeypatch_session(monkeypatch, provider)

    req = route.UpdateProviderRequest(min_age=-1)
    with pytest.raises(HTTPException) as exc:
        await route.update_provider(
            provider_id=provider.id,
            req=req,
            current_user=SimpleNamespace(),
            location_id=None,
        )

    assert exc.value.status_code == 400
    assert "min_age must be 0" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_provider_rejects_age_over_150(monkeypatch):
    """Age > 150 should return 400."""
    provider = _provider()
    _monkeypatch_session(monkeypatch, provider)

    req = route.UpdateProviderRequest(max_age=200)
    with pytest.raises(HTTPException) as exc:
        await route.update_provider(
            provider_id=provider.id,
            req=req,
            current_user=SimpleNamespace(),
            location_id=None,
        )

    assert exc.value.status_code == 400
    assert "max_age must be 0" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_provider_cross_validate_existing_max(monkeypatch):
    """Setting min_age > existing max_age should fail."""
    provider = _provider(max_age=10)
    _monkeypatch_session(monkeypatch, provider)

    req = route.UpdateProviderRequest(min_age=15)
    with pytest.raises(HTTPException) as exc:
        await route.update_provider(
            provider_id=provider.id,
            req=req,
            current_user=SimpleNamespace(),
            location_id=None,
        )

    assert exc.value.status_code == 400
    assert "min_age cannot be greater than max_age" in str(exc.value.detail)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Retell list_providers — age-group filtering
# ═══════════════════════════════════════════════════════════════════════════


def _make_pms_provider(id_val, name):
    """Minimal PMS provider object matching the adapter return shape."""
    return SimpleNamespace(
        id=id_val,
        name=name,
        first_name=name.split()[0],
        last_name=name.split()[-1],
        specialty="General",
        appointment_types=[],
        operatory_ids=[],
    )


class _FakeDBRows:
    """Simulates SQLAlchemy result for age-rule queries."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDBResult:
    def __init__(self, rows):
        self._fake = _FakeDBRows(rows)

    def all(self):
        return self._fake.all()


class _FakeDBSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _FakeDBResult(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


@pytest.mark.asyncio
async def test_list_providers_filters_by_age(monkeypatch):
    """Providers outside the patient's age are excluded."""
    from src.app.retell import handlers

    # Mock _resolve_context
    mock_adapter = SimpleNamespace(
        list_providers=lambda: [
            _make_pms_provider("100", "Dr Pediatric"),   # min=0, max=17
            _make_pms_provider("200", "Dr Adult"),       # min=18, max=65
            _make_pms_provider("300", "Dr Everyone"),    # no age restriction
        ],
    )

    async def mock_resolve():
        return SimpleNamespace(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            adapter=mock_adapter,
        )

    # Make list_providers async
    mock_adapter.list_providers = _async_wrap(mock_adapter.list_providers)

    monkeypatch.setattr(handlers, "_resolve_context", mock_resolve)

    # Mock DB query for age rules
    age_rows = [
        SimpleNamespace(source_id="nh-100", min_age=0, max_age=17),
        SimpleNamespace(source_id="nh-200", min_age=18, max_age=65),
        SimpleNamespace(source_id="nh-300", min_age=None, max_age=None),
    ]

    @asynccontextmanager
    async def fake_db():
        yield _FakeDBSession(age_rows)

    # Patch the binding inside the handlers module — handlers.py imports
    # get_db_session via `from … import …`, which creates a local name. The
    # database module's attribute is no longer reached at call time.
    monkeypatch.setattr(handlers, "get_db_session", fake_db)

    # Patient is 10 years old — should match Dr Pediatric + Dr Everyone
    dob_child = (date.today().replace(year=date.today().year - 10)).isoformat()
    result = await handlers.list_providers({"date_of_birth": dob_child})

    assert result["count"] == 2
    names = [p["name"] for p in result["providers"]]
    assert "Dr Pediatric" in names
    assert "Dr Everyone" in names
    assert "Dr Adult" not in names


@pytest.mark.asyncio
async def test_list_providers_adult_patient(monkeypatch):
    """Adult patient gets adult provider + unrestricted."""
    from src.app.retell import handlers

    mock_adapter = SimpleNamespace()

    async def mock_list_providers():
        return [
            _make_pms_provider("100", "Dr Pediatric"),
            _make_pms_provider("200", "Dr Adult"),
            _make_pms_provider("300", "Dr Everyone"),
        ]

    mock_adapter.list_providers = mock_list_providers

    async def mock_resolve():
        return SimpleNamespace(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            adapter=mock_adapter,
        )

    monkeypatch.setattr(handlers, "_resolve_context", mock_resolve)

    age_rows = [
        SimpleNamespace(source_id="nh-100", min_age=0, max_age=17),
        SimpleNamespace(source_id="nh-200", min_age=18, max_age=65),
        SimpleNamespace(source_id="nh-300", min_age=None, max_age=None),
    ]

    @asynccontextmanager
    async def fake_db():
        yield _FakeDBSession(age_rows)

    monkeypatch.setattr(handlers, "get_db_session", fake_db)

    # Patient is 30 years old
    dob_adult = (date.today().replace(year=date.today().year - 30)).isoformat()
    result = await handlers.list_providers({"date_of_birth": dob_adult})

    assert result["count"] == 2
    names = [p["name"] for p in result["providers"]]
    assert "Dr Adult" in names
    assert "Dr Everyone" in names
    assert "Dr Pediatric" not in names


@pytest.mark.asyncio
async def test_list_providers_no_dob_returns_all(monkeypatch):
    """Without date_of_birth, all providers are returned."""
    from src.app.retell import handlers

    mock_adapter = SimpleNamespace()

    async def mock_list_providers():
        return [
            _make_pms_provider("100", "Dr Pediatric"),
            _make_pms_provider("200", "Dr Adult"),
        ]

    mock_adapter.list_providers = mock_list_providers

    async def mock_resolve():
        return SimpleNamespace(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            adapter=mock_adapter,
        )

    monkeypatch.setattr(handlers, "_resolve_context", mock_resolve)

    result = await handlers.list_providers({})

    assert result["count"] == 2


@pytest.mark.asyncio
async def test_list_providers_invalid_dob_returns_all(monkeypatch):
    """Invalid DOB format should not crash — returns all providers."""
    from src.app.retell import handlers

    mock_adapter = SimpleNamespace()

    async def mock_list_providers():
        return [
            _make_pms_provider("100", "Dr One"),
            _make_pms_provider("200", "Dr Two"),
        ]

    mock_adapter.list_providers = mock_list_providers

    async def mock_resolve():
        return SimpleNamespace(
            institution=SimpleNamespace(id="inst-1"),
            location=SimpleNamespace(id="loc-1"),
            adapter=mock_adapter,
        )

    monkeypatch.setattr(handlers, "_resolve_context", mock_resolve)

    result = await handlers.list_providers({"date_of_birth": "not-a-date"})

    # Should gracefully return all
    assert result["count"] == 2


# ── Async helper ─────────────────────────────────────────────────────────


def _async_wrap(fn):
    """Wrap a sync callable as an async one."""

    async def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper
