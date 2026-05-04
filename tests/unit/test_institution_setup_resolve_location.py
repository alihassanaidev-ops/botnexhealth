"""Direct tests for institution_setup._resolve_institution_location.

The helper is the single chokepoint that picks the location every
mutating institution-setup route operates on. Defaulting to the oldest
active location was operationally dangerous for multi-location
institutions: a missing ?location_id= would silently target the wrong
NexHealth subaccount on PATCH/PUT/POST/DELETE — a cross-clinic write.
These tests pin the explicit-or-single-location contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Sequence

import pytest
from fastapi import HTTPException

from src.app.api.routes.institution_setup import _resolve_institution_location
from src.app.models.user import UserRole


class _ScalarOneOrNone:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _Scalars:
    def __init__(self, items: Sequence[Any]) -> None:
        self._items = list(items)

    def scalars(self) -> "_Scalars":
        return self

    def __iter__(self):
        return iter(self._items)


class _FakeSession:
    """Minimal AsyncSession stand-in.

    Drives the helper through its three queries:
      1. SELECT Institution
      2. (location_id path) SELECT InstitutionLocation by id, OR
      3. (no location_id path) SELECT all active InstitutionLocations
    """

    def __init__(
        self,
        institution: Any,
        location_by_id: Any | None = None,
        active_locations: Sequence[Any] | None = None,
    ) -> None:
        self._results = [_ScalarOneOrNone(institution)]
        if location_by_id is not None:
            self._results.append(_ScalarOneOrNone(location_by_id))
        if active_locations is not None:
            self._results.append(_Scalars(active_locations))

    async def execute(self, _stmt: Any) -> Any:
        if not self._results:
            raise AssertionError("Unexpected extra query")
        return self._results.pop(0)


def _institution_admin(institution_id: str = "inst-1") -> SimpleNamespace:
    return SimpleNamespace(
        id="user-1",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id=institution_id,
        location_id=None,
    )


def _institution(institution_id: str = "inst-1") -> SimpleNamespace:
    return SimpleNamespace(id=institution_id, name="Clinic", is_active=True)


def _location(loc_id: str, slug: str = "main") -> SimpleNamespace:
    return SimpleNamespace(id=loc_id, slug=slug, institution_id="inst-1", is_active=True)


@pytest.mark.asyncio
async def test_single_location_institution_defaults_without_location_id():
    """Single-location institutions keep the convenience default."""
    institution = _institution()
    only_location = _location("loc-1")
    session = _FakeSession(
        institution=institution,
        active_locations=[only_location],
    )

    inst, loc = await _resolve_institution_location(
        _institution_admin(), session, location_id=None
    )

    assert inst is institution
    assert loc is only_location


@pytest.mark.asyncio
async def test_multi_location_institution_rejects_missing_location_id():
    """Multi-location institutions must require an explicit location_id —
    defaulting to oldest silently targets the wrong NexHealth subaccount."""
    institution = _institution()
    locations = [_location("loc-1", "downtown"), _location("loc-2", "uptown")]
    session = _FakeSession(
        institution=institution,
        active_locations=locations,
    )

    with pytest.raises(HTTPException) as exc:
        await _resolve_institution_location(
            _institution_admin(), session, location_id=None
        )

    assert exc.value.status_code == 400
    assert "location_id is required" in exc.value.detail
    assert "Active locations: 2" in exc.value.detail


@pytest.mark.asyncio
async def test_explicit_location_id_passes_through_for_admin():
    """Admin with explicit location_id resolves directly without enumerating."""
    institution = _institution()
    target = _location("loc-2", "uptown")
    session = _FakeSession(
        institution=institution,
        location_by_id=target,
    )

    inst, loc = await _resolve_institution_location(
        _institution_admin(), session, location_id="loc-2"
    )

    assert inst is institution
    assert loc is target


@pytest.mark.asyncio
async def test_no_active_locations_returns_404():
    """An institution with zero active locations is a hard 404, not 400."""
    institution = _institution()
    session = _FakeSession(
        institution=institution,
        active_locations=[],
    )

    with pytest.raises(HTTPException) as exc:
        await _resolve_institution_location(
            _institution_admin(), session, location_id=None
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_staff_user_is_pinned_to_their_location_even_without_query_param():
    """STAFF cannot drift to another location even when location_id is None —
    the helper must hardcode their assigned user.location_id."""
    institution = _institution()
    target = _location("loc-1")
    staff_user = SimpleNamespace(
        id="user-staff",
        role=UserRole.STAFF.value,
        institution_id="inst-1",
        location_id="loc-1",
    )
    session = _FakeSession(
        institution=institution,
        location_by_id=target,
    )

    inst, loc = await _resolve_institution_location(staff_user, session, location_id=None)

    assert inst is institution
    assert loc is target


@pytest.mark.asyncio
async def test_staff_cannot_query_another_location():
    """STAFF passing a different location_id gets 403, not silent override."""
    institution = _institution()
    staff_user = SimpleNamespace(
        id="user-staff",
        role=UserRole.STAFF.value,
        institution_id="inst-1",
        location_id="loc-1",
    )
    session = _FakeSession(institution=institution)

    with pytest.raises(HTTPException) as exc:
        await _resolve_institution_location(staff_user, session, location_id="loc-2")

    assert exc.value.status_code == 403
