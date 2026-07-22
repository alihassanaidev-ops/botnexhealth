"""Super-admin endpoint to invite an institution-scoped user (default:
institution admin) for ANY institution.

Unit-level: the DB session is mocked, so these run without Postgres. RBAC (the
SUPER_ADMIN boundary) is covered separately by test_rbac_route_matrix.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.app.api.routes.admin_users as admin_users
from src.app.api.routes.admin_users import (
    InviteInstitutionUserRequest,
    invite_institution_user,
)
from src.app.models.user import InviteStatus, UserRole

INST_ID = "f325b4d1-afcb-4fb0-8fa2-ef41fb75ed89"


def _route_target(fn):
    return getattr(fn, "__wrapped__", fn)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return None


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Returns queued results in order for each execute() call."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, *_a, **_k):
        return _Result(self._results.pop(0) if self._results else None)


def _admin():
    return SimpleNamespace(id="admin-1", role=UserRole.SUPER_ADMIN.value)


def _install(monkeypatch, *results, created_user=None):
    session = _FakeSession(results)
    monkeypatch.setattr(
        admin_users, "get_db_session", lambda: _SessionContext(session)
    )

    async def _fake_create(**kwargs):
        return SimpleNamespace(id="new-user-1", **kwargs)

    if created_user is not None:
        monkeypatch.setattr(
            admin_users.UserInviteService,
            "create_invited_user",
            lambda self, **kw: _fake_create(**kw),
        )
    # Don't hit the real audit sink.
    async def _noop_audit(**_kw):
        return None

    monkeypatch.setattr(admin_users, "log_audit", _noop_audit)


async def _call(body: InviteInstitutionUserRequest):
    return await _route_target(invite_institution_user)(body=body, current_admin=_admin())


@pytest.mark.asyncio
async def test_invites_institution_admin_happy_path(monkeypatch) -> None:
    institution = SimpleNamespace(id=INST_ID, name="Kadri Dental")
    # execute() order: institution lookup, duplicate-email check (None = no dup)
    _install(monkeypatch, institution, None, created_user=True)
    resp = await _call(
        InviteInstitutionUserRequest(email="Haider@Example.com ", institution_id=INST_ID)
    )
    assert resp.role == UserRole.INSTITUTION_ADMIN.value
    assert resp.institution_id == INST_ID
    assert resp.email == "haider@example.com"  # normalized
    assert resp.invite_status == InviteStatus.PENDING.value


@pytest.mark.asyncio
async def test_rejects_super_admin_role(monkeypatch) -> None:
    _install(monkeypatch)  # never reaches the DB
    with pytest.raises(admin_users.HTTPException) as exc:
        await _call(
            InviteInstitutionUserRequest(
                email="x@example.com", institution_id=INST_ID, role="SUPER_ADMIN"
            )
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_location_role_requires_location(monkeypatch) -> None:
    _install(monkeypatch)
    with pytest.raises(admin_users.HTTPException) as exc:
        await _call(
            InviteInstitutionUserRequest(
                email="x@example.com", institution_id=INST_ID, role="STAFF"
            )
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_institution_admin_must_not_have_location(monkeypatch) -> None:
    _install(monkeypatch)
    with pytest.raises(admin_users.HTTPException) as exc:
        await _call(
            InviteInstitutionUserRequest(
                email="x@example.com",
                institution_id=INST_ID,
                role="INSTITUTION_ADMIN",
                location_id="loc-1",
            )
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_missing_institution_404(monkeypatch) -> None:
    _install(monkeypatch, None)  # institution lookup returns None
    with pytest.raises(admin_users.HTTPException) as exc:
        await _call(
            InviteInstitutionUserRequest(email="x@example.com", institution_id=INST_ID)
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_email_409(monkeypatch) -> None:
    institution = SimpleNamespace(id=INST_ID, name="Kadri Dental")
    existing = SimpleNamespace(id="u9", role=UserRole.STAFF.value)
    _install(monkeypatch, institution, existing)  # dup found
    with pytest.raises(admin_users.HTTPException) as exc:
        await _call(
            InviteInstitutionUserRequest(email="dup@example.com", institution_id=INST_ID)
        )
    assert exc.value.status_code == 409
