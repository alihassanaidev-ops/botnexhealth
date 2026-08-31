"""Call notes: scoping, authorship rules, and PHI handling.

The notes thread is the one place staff type free text about a call, so these
tests pin the three things that make it safe: it inherits the parent call's
tenant scope (never widens it), only the author can rewrite what a note says,
and the body never lands anywhere unencrypted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.app.api.routes import calls as calls_routes
from src.app.models.audit_log import AuditAction, AuditOutcome
from src.app.models.user import UserRole

INSTITUTION_ID = "11111111-1111-1111-1111-111111111111"
LOCATION_ID = "44444444-4444-4444-4444-444444444444"
OTHER_LOCATION_ID = "55555555-5555-5555-5555-555555555555"
CALL_ID = "33333333-3333-3333-3333-333333333333"
AUTHOR_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_USER_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


# ── Fakes ─────────────────────────────────────────────────────────────────────


class _ExecuteResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value if isinstance(self.value, list) else [self.value]


class _FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        if not self.results:
            raise AssertionError("No fake execute result left")
        return _ExecuteResult(self.results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1
        # A real commit flushes first, which is where SQLAlchemy applies the
        # Python-side column defaults (the uuid primary key, created_at, …).
        # The route serializes the object straight afterwards, so the fake has
        # to do the same or it would fail on values production always has.
        for obj in self.added:
            self._apply_column_defaults(obj)

    @staticmethod
    def _apply_column_defaults(obj):
        table = getattr(type(obj), "__table__", None)
        if table is None:
            return
        for column in table.columns:
            default = column.default
            if default is None or not (default.is_scalar or default.is_callable):
                continue
            if getattr(obj, column.key, None) is not None:
                continue
            value = default.arg(None) if default.is_callable else default.arg
            setattr(obj, column.key, value)

    async def refresh(self, _obj):
        return None


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return None


def _route_target(fn):
    """Unwrap the rate-limit decorator to call the handler directly."""
    return getattr(fn, "__wrapped__", fn)


def _install_session(monkeypatch: pytest.MonkeyPatch, *results):
    session = _FakeSession(*results)
    monkeypatch.setattr(calls_routes, "get_db_session", lambda: _SessionContext(session))
    return session


def _capture_audit(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    rows: list[dict] = []
    monkeypatch.setattr(
        calls_routes, "log_audit_background", lambda **kwargs: rows.append(kwargs)
    )
    return rows


def _user(
    role: str = UserRole.INSTITUTION_ADMIN.value,
    *,
    user_id: str = AUTHOR_ID,
    location_id: str | None = None,
    email: str = "sarah@olivetree.example",
):
    return SimpleNamespace(
        id=user_id,
        role=role,
        institution_id=INSTITUTION_ID,
        location_id=location_id,
        email=email,
    )


def _call(location_id: str | None = LOCATION_ID):
    return SimpleNamespace(
        id=CALL_ID,
        institution_id=INSTITUTION_ID,
        location_id=location_id,
        contact=None,
        contact_id=None,
    )


def _note(
    note_id: str = "99999999-9999-9999-9999-999999999999",
    *,
    author_user_id: str | None = AUTHOR_ID,
    author_email: str = "sarah@olivetree.example",
    body: str = "Called back, left voicemail.",
    edited_at: datetime | None = None,
):
    return SimpleNamespace(
        id=note_id,
        call_id=CALL_ID,
        institution_id=INSTITUTION_ID,
        location_id=LOCATION_ID,
        author_user_id=author_user_id,
        author_email=author_email,
        body=body,
        created_at=datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
        edited_at=edited_at,
        deleted_at=None,
        deleted_by_user_id=None,
    )


# ── Model ─────────────────────────────────────────────────────────────────────


def test_note_body_is_encrypted_at_rest():
    """The column holds ciphertext; only the property yields the text back."""
    from src.app.models.call_note import CallNote

    note = CallNote()
    note.body = "Patient reports pain in lower left molar"

    assert note.body_encrypted is not None
    assert "molar" not in note.body_encrypted
    assert note.body == "Patient reports pain in lower left molar"


# ── Reading the thread ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_notes_resolves_edit_and_delete_per_caller(monkeypatch):
    """The API decides who may edit/delete — the client must not re-derive it."""
    _capture_audit(monkeypatch)
    mine = _note("11111111-0000-0000-0000-000000000001", author_user_id=AUTHOR_ID)
    theirs = _note(
        "11111111-0000-0000-0000-000000000002",
        author_user_id=OTHER_USER_ID,
        author_email="dr.kim@olivetree.example",
    )
    _install_session(monkeypatch, _call(), [mine, theirs])

    resp = await _route_target(calls_routes.list_call_notes)(
        request=None,
        call_id=CALL_ID,
        current_user=_user(UserRole.STAFF.value, location_id=LOCATION_ID),
    )

    assert resp.total == 2
    own, other = resp.items
    # Author of the first note: full control over it.
    assert (own.can_edit, own.can_delete) == (True, True)
    # Staff on someone else's note: read-only.
    assert (other.can_edit, other.can_delete) == (False, False)
    # The email is the attribution the UI renders.
    assert other.author_email == "dr.kim@olivetree.example"


@pytest.mark.asyncio
async def test_location_admin_may_delete_a_colleagues_note(monkeypatch):
    """Admins moderate their scope; that's delete-only, never edit."""
    _capture_audit(monkeypatch)
    theirs = _note(author_user_id=OTHER_USER_ID, author_email="dr.kim@olivetree.example")
    _install_session(monkeypatch, _call(), [theirs])

    resp = await _route_target(calls_routes.list_call_notes)(
        request=None,
        call_id=CALL_ID,
        current_user=_user(UserRole.LOCATION_ADMIN.value, location_id=LOCATION_ID),
    )

    item = resp.items[0]
    assert item.can_delete is True
    assert item.can_edit is False


@pytest.mark.asyncio
async def test_notes_on_another_locations_call_are_not_found(monkeypatch):
    """A note never widens the call's scope: no call, no thread."""
    _capture_audit(monkeypatch)
    # _get_scoped_call adds Call.location_id to the WHERE clause for
    # location-scoped roles, so the lookup comes back empty.
    _install_session(monkeypatch, None)

    with pytest.raises(HTTPException) as exc:
        await _route_target(calls_routes.list_call_notes)(
            request=None,
            call_id=CALL_ID,
            current_user=_user(UserRole.STAFF.value, location_id=OTHER_LOCATION_ID),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_super_admin_is_blocked_and_the_attempt_is_audited(monkeypatch):
    """Note bodies are inline PHI, so the reveal-gate rule applies here too."""
    rows = _capture_audit(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await _route_target(calls_routes.list_call_notes)(
            request=None,
            call_id=CALL_ID,
            current_user=_user(UserRole.SUPER_ADMIN.value),
        )

    assert exc.value.status_code == 403
    assert rows and rows[0]["outcome"] == AuditOutcome.FAILURE_UNAUTHORIZED
    assert rows[0]["metadata"]["reason"] == "super_admin_call_notes_blocked"


# ── Writing ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_note_inherits_call_scope_and_stamps_the_author(monkeypatch):
    rows = _capture_audit(monkeypatch)
    session = _install_session(monkeypatch, _call())

    body = calls_routes.CreateCallNoteRequest(body="  Booked for Tue 3pm  ")
    out = await _route_target(calls_routes.create_call_note)(
        request=None,
        call_id=CALL_ID,
        body=body,
        current_user=_user(UserRole.STAFF.value, location_id=LOCATION_ID),
    )

    (saved,) = session.added
    # Scope is copied from the call, not from the caller — an institution admin
    # posting on a location's call must not produce an unscoped note.
    assert saved.institution_id == INSTITUTION_ID
    assert saved.location_id == LOCATION_ID
    assert saved.author_user_id == AUTHOR_ID
    assert saved.author_email == "sarah@olivetree.example"
    # Surrounding whitespace is trimmed before it is stored.
    assert saved.body == "Booked for Tue 3pm"
    assert out.body == "Booked for Tue 3pm"
    assert out.can_edit is True

    (audit,) = rows
    assert audit["action"] == AuditAction.CALL_NOTE_CREATE
    assert audit["outcome"] == AuditOutcome.SUCCESS
    # The body is PHI — only its length may appear in the audit metadata.
    assert audit["metadata"]["body_length"] == len("Booked for Tue 3pm")
    assert "Booked" not in str(audit["metadata"])


@pytest.mark.asyncio
async def test_whitespace_only_note_is_rejected(monkeypatch):
    _capture_audit(monkeypatch)
    _install_session(monkeypatch, _call())

    with pytest.raises(HTTPException) as exc:
        await _route_target(calls_routes.create_call_note)(
            request=None,
            call_id=CALL_ID,
            body=calls_routes.CreateCallNoteRequest(body="   \n  "),
            current_user=_user(UserRole.STAFF.value, location_id=LOCATION_ID),
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_only_the_author_may_edit_a_note(monkeypatch):
    """An admin can remove a note but never rewrite it under another name."""
    rows = _capture_audit(monkeypatch)
    theirs = _note(author_user_id=OTHER_USER_ID, author_email="dr.kim@olivetree.example")
    _install_session(monkeypatch, _call(), theirs)

    with pytest.raises(HTTPException) as exc:
        await _route_target(calls_routes.update_call_note)(
            request=None,
            call_id=CALL_ID,
            note_id=theirs.id,
            body=calls_routes.UpdateCallNoteRequest(body="rewritten"),
            current_user=_user(UserRole.INSTITUTION_ADMIN.value),
        )

    assert exc.value.status_code == 403
    assert theirs.body == "Called back, left voicemail."
    assert rows[-1]["metadata"]["reason"] == "not_note_author"


@pytest.mark.asyncio
async def test_author_edit_marks_the_note_edited(monkeypatch):
    _capture_audit(monkeypatch)
    mine = _note(author_user_id=AUTHOR_ID)
    _install_session(monkeypatch, _call(), mine)

    out = await _route_target(calls_routes.update_call_note)(
        request=None,
        call_id=CALL_ID,
        note_id=mine.id,
        body=calls_routes.UpdateCallNoteRequest(body="Called back, spoke to patient."),
        current_user=_user(UserRole.STAFF.value, location_id=LOCATION_ID),
    )

    assert out.body == "Called back, spoke to patient."
    assert out.edited_at is not None


@pytest.mark.asyncio
async def test_staff_cannot_delete_someone_elses_note(monkeypatch):
    rows = _capture_audit(monkeypatch)
    theirs = _note(author_user_id=OTHER_USER_ID)
    _install_session(monkeypatch, _call(), theirs)

    with pytest.raises(HTTPException) as exc:
        await _route_target(calls_routes.delete_call_note)(
            request=None,
            call_id=CALL_ID,
            note_id=theirs.id,
            current_user=_user(
                UserRole.STAFF.value, user_id=AUTHOR_ID, location_id=LOCATION_ID
            ),
        )

    assert exc.value.status_code == 403
    assert theirs.deleted_at is None
    assert rows[-1]["metadata"]["reason"] == "not_author_or_admin"


@pytest.mark.asyncio
async def test_admin_delete_is_soft_so_the_record_survives(monkeypatch):
    rows = _capture_audit(monkeypatch)
    theirs = _note(author_user_id=OTHER_USER_ID)
    session = _install_session(monkeypatch, _call(), theirs)

    await _route_target(calls_routes.delete_call_note)(
        request=None,
        call_id=CALL_ID,
        note_id=theirs.id,
        current_user=_user(UserRole.INSTITUTION_ADMIN.value),
    )

    # The row is stamped, not dropped — it stays available for audit review.
    assert theirs.deleted_at is not None
    assert theirs.deleted_by_user_id == AUTHOR_ID
    assert theirs.body == "Called back, left voicemail."
    assert session.commits == 1

    audit = rows[-1]
    assert audit["action"] == AuditAction.CALL_NOTE_DELETE
    assert audit["outcome"] == AuditOutcome.SUCCESS
    assert audit["metadata"]["own_note"] is False
