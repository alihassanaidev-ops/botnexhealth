"""Super-admin user management endpoints (`/admin/users`).

These replace the one-off ``delete_user_and_location`` script: a super admin can
remove (soft-delete) or reinvite any non-super-admin user from the dashboard.
"Remove" sets ``deleted_at`` / ``is_active=False`` so the email frees up for
re-invite via the partial unique index on ``users(email) WHERE deleted_at IS
NULL``.

The integration suite has no live DB, so we mock ``get_db_session`` per test
(same pattern as ``test_admin_mfa_reset.py``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from src.app.api.deps import get_current_admin
from src.app.api.routes import admin_users as admin_users_routes
from src.app.main import app
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.user import InviteStatus, User, UserRole

ADMIN_ID = "00000000-0000-0000-0000-000000000001"
INSTITUTION_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture
def super_admin() -> User:
    return User(
        id=ADMIN_ID,
        email="root@example.com",
        role=UserRole.SUPER_ADMIN.value,
        institution_id=INSTITUTION_ID,
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
    )


@pytest.fixture
def override_admin(super_admin):
    app.dependency_overrides[get_current_admin] = lambda: super_admin
    try:
        yield super_admin
    finally:
        app.dependency_overrides.pop(get_current_admin, None)


def _make_user(
    *,
    user_id: str = "11111111-1111-1111-1111-111111111111",
    role: str = UserRole.LOCATION_ADMIN.value,
    location_id: str | None = "cccccccc-cccc-cccc-cccc-cccccccccccc",
    invite_status: str = InviteStatus.PENDING.value,
) -> User:
    return User(
        id=user_id,
        email="loc-admin@clinic.test",
        role=role,
        institution_id=INSTITUTION_ID,
        location_id=location_id,
        is_active=True,
        invite_status=invite_status,
    )


def _stub_db_returning(target: User | None):
    """Patch get_db_session so the User lookup resolves to ``target``."""

    @asynccontextmanager
    async def _ctx(*_args, **_kwargs):
        session = AsyncMock()
        result = AsyncMock()
        result.scalar_one_or_none = lambda: target
        session.execute = AsyncMock(return_value=result)
        yield session

    return patch.object(admin_users_routes, "get_db_session", _ctx)


# ---------------------------------------------------------------------------
# remove_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_user_soft_deletes_and_audits(
    async_client: AsyncClient, override_admin, audit_log_entries
):
    target = _make_user()
    with _stub_db_returning(target):
        resp = await async_client.delete(f"/api/admin/users/{target.id}")

    assert resp.status_code == 200
    assert resp.json()["user_id"] == target.id
    # Soft-delete primitive ran: access revoked + tombstoned (frees the email).
    assert target.is_active is False
    assert target.deleted_at is not None

    entries = await audit_log_entries()
    matches = [
        e
        for e in entries
        if e.action == AuditAction.USER_DELETE and e.outcome == AuditOutcome.SUCCESS
    ]
    assert len(matches) == 1
    assert matches[0].target_resource == f"user:{target.id}"
    assert matches[0].metadata["target_user_id"] == target.id
    assert matches[0].metadata["target_role"] == UserRole.LOCATION_ADMIN.value


@pytest.mark.asyncio
async def test_remove_user_cannot_remove_self(async_client: AsyncClient, override_admin):
    with _stub_db_returning(override_admin):
        resp = await async_client.delete(f"/api/admin/users/{ADMIN_ID}")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_remove_user_cannot_remove_super_admin(
    async_client: AsyncClient, override_admin
):
    other_super = _make_user(
        user_id="22222222-2222-2222-2222-222222222222",
        role=UserRole.SUPER_ADMIN.value,
    )
    with _stub_db_returning(other_super):
        resp = await async_client.delete(f"/api/admin/users/{other_super.id}")
    assert resp.status_code == 403
    # Guard fired before mutation.
    assert other_super.deleted_at is None


@pytest.mark.asyncio
async def test_remove_user_404_when_missing_or_already_removed(
    async_client: AsyncClient, override_admin
):
    with _stub_db_returning(None):
        resp = await async_client.delete(
            "/api/admin/users/99999999-9999-9999-9999-999999999999"
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_remove_user_requires_admin(async_client: AsyncClient):
    """No admin override → dependency rejects (401/403), never reaches the handler."""
    resp = await async_client.delete(
        "/api/admin/users/11111111-1111-1111-1111-111111111111"
    )
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# reinvite_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reinvite_user_rotates_invite_and_audits(
    async_client: AsyncClient, override_admin, audit_log_entries
):
    target = _make_user()
    with _stub_db_returning(target), patch.object(
        admin_users_routes.UserInviteService,
        "reinvite_user",
        new=AsyncMock(return_value=target),
    ) as reinvite_call:
        resp = await async_client.post(f"/api/admin/users/{target.id}/reinvite")

    assert resp.status_code == 200
    reinvite_call.assert_awaited_once()

    entries = await audit_log_entries()
    matches = [e for e in entries if e.action == AuditAction.USER_REINVITED]
    assert len(matches) == 1
    assert matches[0].metadata["target_user_id"] == target.id


@pytest.mark.asyncio
async def test_reinvite_user_blocks_super_admin(async_client: AsyncClient, override_admin):
    other_super = _make_user(
        user_id="22222222-2222-2222-2222-222222222222",
        role=UserRole.SUPER_ADMIN.value,
    )
    with _stub_db_returning(other_super), patch.object(
        admin_users_routes.UserInviteService, "reinvite_user", new=AsyncMock()
    ) as reinvite_call:
        resp = await async_client.post(f"/api/admin/users/{other_super.id}/reinvite")
    assert resp.status_code == 403
    reinvite_call.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_users_returns_mapped_rows(async_client: AsyncClient, override_admin):
    # institution_id/location_id None → no enrichment queries; paginate is
    # patched so we don't need a live DB.
    row = _make_user(location_id=None)
    row.institution_id = None
    with _stub_db_returning(None), patch.object(
        admin_users_routes,
        "paginate",
        new=AsyncMock(return_value=([row], 1)),
    ):
        resp = await async_client.get("/api/admin/users?status=all")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == row.email
    assert body["items"][0]["role"] == UserRole.LOCATION_ADMIN.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected_invite",
    [("active", "ACCEPTED"), ("pending", "PENDING")],
)
async def test_list_users_active_and_pending_are_mutually_exclusive(
    async_client: AsyncClient, override_admin, status, expected_invite
):
    """`active` and `pending` both exclude removed rows and filter on opposite
    invite_status values, so the filter matches its label."""
    captured = {}

    async def _capture(query, *, page, size):
        captured["sql"] = str(
            query.statement.compile(compile_kwargs={"literal_binds": True})
        )
        return ([], 0)

    with _stub_db_returning(None), patch.object(
        admin_users_routes, "paginate", new=_capture
    ):
        resp = await async_client.get(f"/api/admin/users?status={status}")

    assert resp.status_code == 200
    sql = captured["sql"]
    assert "deleted_at IS NULL" in sql
    assert f"invite_status = '{expected_invite}'" in sql
