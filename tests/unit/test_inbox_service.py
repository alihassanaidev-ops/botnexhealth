"""Unit tests for inbox scoping.

Scoping is where a mistake leaks one clinic's patients to another, so these
lean on the narrowing rather than on the happy path. Two properties matter most:

* A location-bound user sees only their own location.
* A group admin sees **figures**, never message content — that role is
  deliberately kept off routes carrying patient information, so the inbox must
  refuse it rather than merely hide content in the UI.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.models.user import UserRole
from src.app.services.email.inbox_service import (
    InboxAccessError,
    InboxScope,
    InboxService,
    _mask,
    scope_for_user,
)


def _scope(role, **kw) -> InboxScope:
    base = dict(user_id="u-1", institution_id="inst-1", location_id=None, group_id=None)
    base.update(kw)
    return InboxScope(role=role, **base)


SUPER = _scope(UserRole.SUPER_ADMIN.value, institution_id=None)
GROUP = _scope(UserRole.GROUP_ADMIN.value, institution_id=None, group_id="grp-1")
INST = _scope(UserRole.INSTITUTION_ADMIN.value)
LOC_ADMIN = _scope(UserRole.LOCATION_ADMIN.value, location_id="loc-1")
STAFF = _scope(UserRole.STAFF.value, location_id="loc-1")


# ---------------------------------------------------------------------------
# Capability matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope,content,reply,assign,location_bound",
    [
        (SUPER, True, True, True, False),
        (GROUP, False, False, False, False),
        (INST, True, True, True, False),
        (LOC_ADMIN, True, True, True, True),
        (STAFF, True, True, False, True),
    ],
)
def test_capability_matrix(scope, content, reply, assign, location_bound):
    assert scope.may_read_content is content
    assert scope.may_reply is reply
    assert scope.may_assign is assign
    assert scope.is_location_bound is location_bound


def test_only_super_admin_is_platform_wide():
    assert SUPER.is_platform_wide is True
    for scope in (GROUP, INST, LOC_ADMIN, STAFF):
        assert scope.is_platform_wide is False


def test_staff_cannot_assign_but_can_reply():
    """Staff answer patients; deciding who owns a conversation is an admin
    action."""
    assert STAFF.may_reply is True
    assert STAFF.may_assign is False


# ---------------------------------------------------------------------------
# Group oversight is refused conversation access outright
# ---------------------------------------------------------------------------


def _service():
    return InboxService(AsyncMock())


def test_group_admin_cannot_list_conversations():
    with pytest.raises(InboxAccessError):
        asyncio.run(_service().list_threads(GROUP))


def test_group_admin_cannot_read_a_conversation():
    with pytest.raises(InboxAccessError):
        asyncio.run(_service().get_messages(GROUP, "t-1"))


def test_group_admin_cannot_assign():
    with pytest.raises(InboxAccessError):
        asyncio.run(_service().assign(GROUP, "t-1", "u-2"))


def test_group_admin_cannot_resolve():
    with pytest.raises(InboxAccessError):
        asyncio.run(_service().resolve(GROUP, "t-1"))


def test_staff_cannot_assign_through_the_service():
    """The capability flag is not advisory — the service enforces it."""
    with pytest.raises(InboxAccessError):
        asyncio.run(_service().assign(STAFF, "t-1", "u-2"))


# ---------------------------------------------------------------------------
# Query narrowing
# ---------------------------------------------------------------------------


def _compiled(scope) -> str:
    query = asyncio.run(_service()._scoped_threads(scope))
    return str(query.compile(compile_kwargs={"literal_binds": True}))


def test_super_admin_query_is_unfiltered():
    sql = _compiled(SUPER)
    assert "institution_id =" not in sql
    assert "location_id =" not in sql


def test_institution_admin_query_is_scoped_to_the_institution():
    sql = _compiled(INST)
    assert "institution_id" in sql
    assert "location_id =" not in sql


def test_location_bound_query_is_scoped_to_the_location():
    for scope in (LOC_ADMIN, STAFF):
        sql = _compiled(scope)
        assert "institution_id" in sql
        assert "location_id" in sql


def test_group_query_is_limited_to_member_institutions():
    sql = _compiled(GROUP)
    assert "group_id" in sql


def test_group_admin_without_a_group_sees_nothing():
    """Fail closed: a misconfigured group admin must not fall through to
    platform-wide visibility."""
    sql = _compiled(_scope(UserRole.GROUP_ADMIN.value, institution_id=None, group_id=None))
    assert "false" in sql.lower()


def test_user_without_an_institution_sees_nothing():
    sql = _compiled(_scope(UserRole.INSTITUTION_ADMIN.value, institution_id=None))
    assert "false" in sql.lower()


def test_location_user_without_a_location_sees_nothing():
    sql = _compiled(_scope(UserRole.STAFF.value, location_id=None))
    assert "false" in sql.lower()


# ---------------------------------------------------------------------------
# Scope construction
# ---------------------------------------------------------------------------


def test_scope_is_built_from_the_user():
    user = MagicMock()
    user.role = UserRole.LOCATION_ADMIN.value
    user.id = "u-9"
    user.institution_id = "inst-9"
    user.location_id = "loc-9"
    user.group_id = None

    scope = scope_for_user(user)

    assert scope.role == UserRole.LOCATION_ADMIN.value
    assert scope.institution_id == "inst-9"
    assert scope.location_id == "loc-9"
    assert scope.is_location_bound is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address,expected",
    [
        ("jane@example.com", "j***@example.com"),
        (None, None),
        ("notanemail", None),
    ],
)
def test_mask(address, expected):
    assert _mask(address) == expected
