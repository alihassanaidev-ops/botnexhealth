"""Multi-location user authorization: membership checks + RLS rebinding.

A LOCATION_ADMIN / STAFF account may be assigned several locations (primary on
``users.location_id`` plus ``user_locations`` rows). These tests pin the two
load-bearing behaviors:

- scope checks are *membership* against ``User.allowed_location_ids`` (an
  unassigned location still 403s; any assigned location passes), and
- once validated, the request's RLS context is re-bound to the chosen
  location, so row policies follow the location being acted on instead of the
  primary the session authenticated with.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.api.deps_scope import (
    assert_location_scope,
    bind_active_location,
    resolve_location_scope,
)
from src.app.database import (
    RlsContext,
    clear_current_rls_context,
    current_rls_context,
    set_current_rls_context,
)
from src.app.models.user import User, UserRole
from src.app.models.user_location import UserLocation


def _staff(*, location_id: str = "loc-primary", extra: set[str] = frozenset()):
    return SimpleNamespace(
        id="user-1",
        role=UserRole.STAFF.value,
        institution_id="inst-1",
        location_id=location_id,
        allowed_location_ids={location_id, *extra},
    )


@pytest.fixture(autouse=True)
def _clean_rls_context():
    clear_current_rls_context()
    yield
    clear_current_rls_context()


# ── membership checks ─────────────────────────────────────────────────────────


def test_primary_location_passes_scope_check():
    assert_location_scope(_staff(), "loc-primary")


def test_extra_assigned_location_passes_scope_check():
    assert_location_scope(_staff(extra={"loc-b"}), "loc-b")


def test_unassigned_location_is_rejected():
    with pytest.raises(HTTPException) as exc:
        assert_location_scope(_staff(extra={"loc-b"}), "loc-c")
    assert exc.value.status_code == 403


def test_missing_location_is_rejected():
    with pytest.raises(HTTPException) as exc:
        assert_location_scope(_staff(), None)
    assert exc.value.status_code == 403


def test_institution_admin_is_not_location_scoped():
    admin = SimpleNamespace(
        role=UserRole.INSTITUTION_ADMIN.value,
        location_id=None,
        allowed_location_ids=set(),
    )
    # Any location passes — institution admins are scoped elsewhere.
    assert_location_scope(admin, "loc-anything")


# ── RLS rebinding ─────────────────────────────────────────────────────────────


def test_bind_active_location_rebinds_context_location_only():
    set_current_rls_context(
        RlsContext(
            context_type="user",
            user_id="user-1",
            role=UserRole.STAFF.value,
            institution_id="inst-1",
            location_id="loc-primary",
        )
    )

    bind_active_location(_staff(extra={"loc-b"}), "loc-b")

    context = current_rls_context()
    assert context is not None
    assert context.location_id == "loc-b"
    # Everything else is untouched.
    assert context.user_id == "user-1"
    assert context.institution_id == "inst-1"
    assert context.role == UserRole.STAFF.value


def test_bind_active_location_without_context_is_a_noop():
    bind_active_location(_staff(), "loc-primary")
    assert current_rls_context() is None


def test_resolve_location_scope_defaults_to_primary_and_binds():
    set_current_rls_context(
        RlsContext(
            context_type="user",
            user_id="user-1",
            role=UserRole.STAFF.value,
            institution_id="inst-1",
            location_id="loc-primary",
        )
    )
    user = _staff(extra={"loc-b"})

    assert resolve_location_scope(user, None) == "loc-primary"
    assert current_rls_context().location_id == "loc-primary"

    assert resolve_location_scope(user, "loc-b") == "loc-b"
    assert current_rls_context().location_id == "loc-b"

    with pytest.raises(HTTPException):
        resolve_location_scope(user, "loc-c")


# ── the model property ────────────────────────────────────────────────────────


def test_allowed_location_ids_is_primary_plus_extras():
    user = User(
        id="u-1",
        email="staff@clinic.dev",
        role=UserRole.STAFF.value,
        institution_id="inst-1",
        location_id="loc-primary",
    )
    user.extra_locations = [
        UserLocation(user_id="u-1", institution_id="inst-1", location_id="loc-b"),
        UserLocation(user_id="u-1", institution_id="inst-1", location_id="loc-c"),
    ]
    assert user.allowed_location_ids == {"loc-primary", "loc-b", "loc-c"}


def test_allowed_location_ids_single_location_user():
    user = User(
        id="u-2",
        email="solo@clinic.dev",
        role=UserRole.LOCATION_ADMIN.value,
        institution_id="inst-1",
        location_id="loc-only",
    )
    assert user.allowed_location_ids == {"loc-only"}


def test_allowed_location_ids_institution_admin_is_empty():
    user = User(
        id="u-3",
        email="admin@clinic.dev",
        role=UserRole.INSTITUTION_ADMIN.value,
        institution_id="inst-1",
        location_id=None,
    )
    assert user.allowed_location_ids == set()
