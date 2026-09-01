"""Named permissions for high-consequence actions (Item 33).

The rule the scope note insists on, and the one these tests exist to hold:
**replay and conflict resolution sit above ordinary campaign editing.** Someone
trusted to fix the wording of a reminder is not thereby trusted to force an
appointment into a live practice's diary.

The last test is the one that earns its keep over time — it fails when a new
high-consequence endpoint appears without a permission on it, which is exactly
how two of these four came to have no check at all.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from src.app.api.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    has_permission,
    permissions_for,
    require_permission,
)
from src.app.main import app
from src.app.models.user import User, UserRole


def _user(role: UserRole) -> User:
    return User(
        id="11111111-1111-1111-1111-111111111111",
        email=f"{role.value.lower()}@example.com",
        role=role.value,
        is_active=True,
    )


def _check(role: UserRole, permission: Permission) -> User:
    """Run the dependency exactly as FastAPI would, minus the injection."""
    dependency = require_permission(permission)
    return asyncio.run(dependency(_user(role)))


# ── The rule the item is actually about ──────────────────────────────


def test_editing_a_campaign_does_not_grant_forcing_a_booking() -> None:
    """The scope note's central requirement, stated as a test.

    A location admin configures campaigns. That must not carry the ability to
    replay a write or override a conflict, both of which reach a real diary.
    """
    location_admin = _user(UserRole.LOCATION_ADMIN)

    assert has_permission(location_admin, Permission.CAMPAIGN_CONFIGURE)
    assert not has_permission(location_admin, Permission.WRITE_REPLAY)
    assert not has_permission(location_admin, Permission.WRITE_RESOLVE_CONFLICT)


def test_staff_hold_none_of_the_four() -> None:
    """Including sync status, which STAFF could read before Item 33."""
    assert permissions_for(UserRole.STAFF.value) == frozenset()


def test_institution_admin_holds_all_four() -> None:
    assert permissions_for(UserRole.INSTITUTION_ADMIN.value) == frozenset(Permission)


def test_group_admin_gains_nothing() -> None:
    """Read-only oversight, explicitly never on PHI or setup routes."""
    assert permissions_for(UserRole.GROUP_ADMIN.value) == frozenset()


# ── Enforcement ──────────────────────────────────────────────────────


@pytest.mark.parametrize("permission", list(Permission))
def test_an_institution_admin_is_admitted(permission: Permission) -> None:
    assert _check(UserRole.INSTITUTION_ADMIN, permission) is not None


@pytest.mark.parametrize("permission", list(Permission))
def test_staff_are_refused_every_permission(permission: Permission) -> None:
    with pytest.raises(HTTPException) as exc:
        _check(UserRole.STAFF, permission)
    assert exc.value.status_code == 403
    # The refusal names the permission, not the role, so an operator reading it
    # knows which key to grant rather than guessing at the hierarchy.
    assert permission.value in exc.value.detail


def test_a_location_admin_is_refused_the_dangerous_pair() -> None:
    for permission in (Permission.WRITE_REPLAY, Permission.WRITE_RESOLVE_CONFLICT):
        with pytest.raises(HTTPException) as exc:
            _check(UserRole.LOCATION_ADMIN, permission)
        assert exc.value.status_code == 403


def test_a_location_admin_is_admitted_to_the_ordinary_pair() -> None:
    assert _check(UserRole.LOCATION_ADMIN, Permission.SYNC_READ) is not None
    assert _check(UserRole.LOCATION_ADMIN, Permission.CAMPAIGN_CONFIGURE) is not None


# ── Failing closed ───────────────────────────────────────────────────


def test_no_user_holds_nothing() -> None:
    assert not has_permission(None, Permission.WRITE_REPLAY)


def test_an_unmapped_role_holds_nothing() -> None:
    """A role added to UserRole without a line here gets nothing, not everything."""
    assert permissions_for("SOME_FUTURE_ROLE") == frozenset()
    assert permissions_for(None) == frozenset()


def test_every_role_is_mapped() -> None:
    """A new role must be given an explicit entry, even an empty one.

    Without this, adding a role silently grants it nothing — which is the safe
    default, but silently. This makes the decision visible in review.
    """
    unmapped = {role.value for role in UserRole} - set(ROLE_PERMISSIONS)
    assert unmapped == set()


# ── Coverage: the test that catches the next one ─────────────────────


#: Routes reachable in this repo that perform one of the four actions. A new
#: endpoint doing any of them belongs here and must carry the permission.
EXPECTED_PERMISSION_ROUTES: dict[str, Permission] = {
    "GET /api/institution/appointment-sync": Permission.SYNC_READ,
    "POST /api/admin/dead-letter-events/{event_id}/replay": Permission.WRITE_REPLAY,
    "POST /api/compliance/quiet-hours/exceptions": Permission.CAMPAIGN_CONFIGURE,
    "PATCH /api/compliance/quiet-hours/exceptions/{exception_id}": (
        Permission.CAMPAIGN_CONFIGURE
    ),
    "DELETE /api/compliance/quiet-hours/exceptions/{exception_id}": (
        Permission.CAMPAIGN_CONFIGURE
    ),
}


def _routes_with_permissions() -> dict[str, set[str]]:
    """Every route carrying a require_permission dependency, by dependency name."""
    found: dict[str, set[str]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        names: set[str] = set()

        def walk(dependant) -> None:
            for dependency in dependant.dependencies:
                name = getattr(dependency.call, "__name__", "")
                if name.startswith("require_"):
                    names.add(name)
                walk(dependency)

        walk(route.dependant)
        if names:
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                found[f"{method} {route.path}"] = names
    return found


def test_each_named_action_carries_its_permission() -> None:
    actual = _routes_with_permissions()
    for route_key, permission in EXPECTED_PERMISSION_ROUTES.items():
        assert route_key in actual, f"{route_key} lost its permission dependency"
        expected_name = f"require_{permission.name.lower()}"
        assert expected_name in actual[route_key], (
            f"{route_key} should require {permission.value}, "
            f"found {sorted(actual[route_key])}"
        )


def test_no_route_carries_a_permission_without_being_declared() -> None:
    """The other direction: a permission applied somewhere nobody recorded.

    Not a security hole in itself, but it means the list above has stopped
    describing where these actions live, and that list is what the next person
    reads to find them.
    """
    undeclared = set(_routes_with_permissions()) - set(EXPECTED_PERMISSION_ROUTES)
    assert undeclared == set(), (
        "these routes require a permission but are not in "
        f"EXPECTED_PERMISSION_ROUTES: {sorted(undeclared)}"
    )


def test_conflict_resolution_has_no_endpoint_here() -> None:
    """Documents a real gap rather than hiding it.

    WRITE_RESOLVE_CONFLICT is defined but enforced nowhere in this repo, because
    the button that resolves a GoTracker conflict lives in the Cloud Service.
    Item 33 is not fully delivered until that side gates it. If an endpoint for
    it ever appears here, this test fails and someone has to decide deliberately
    whether it is the right home for it.
    """
    enforced = {
        name for names in _routes_with_permissions().values() for name in names
    }
    assert "require_write_resolve_conflict" not in enforced
