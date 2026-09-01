"""Issuing, revoking and rotating a clinic's form credentials.

The property that matters most: the token exists in a response exactly once, at
creation and rotation, and nowhere else — not in a list, not in an update, not
in the row it came from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.api.deps import get_current_institution_admin
from src.app.api.routes.enquiry_intake_admin import _to_response, router
from src.app.database import get_db_session_dep
from src.app.models.enquiry_intake_source import (
    EnquiryIntakeSource,
    hash_intake_token,
)


def _user(institution_id="inst-1"):
    user = MagicMock()
    user.institution_id = institution_id
    return user


def _row(**over):
    row = EnquiryIntakeSource(
        id="src-1",
        institution_id="inst-1",
        location_id="loc-1",
        label="Website form",
        token_hash=hash_intake_token("old-token-old-token-old-token"),
        source_name="typeform",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    for key, value in over.items():
        setattr(row, key, value)
    return row


def _session(row=None, location_ok=True):
    session = AsyncMock()
    added: list = []
    session.add = MagicMock(side_effect=added.append)

    async def _flush():
        # A real flush applies the column defaults. Without this the row looks
        # like one that was never inserted, which it never is in production.
        for obj in added:
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)
            if getattr(obj, "is_active", None) is None:
                obj.is_active = True

    session.flush = AsyncMock(side_effect=_flush)

    results = []

    async def _execute(_stmt):
        result = MagicMock()
        value = results.pop(0) if results else None
        result.scalar_one_or_none.return_value = value
        result.scalars.return_value.all.return_value = [row] if row else []
        return result

    session.execute = AsyncMock(side_effect=_execute)
    session._queue = results
    session._location_ok = location_ok
    return session


def _client(session, user=None):
    """The routes carry a rate-limit decorator that needs a real Request, so
    they are exercised through the app rather than called directly."""
    app = FastAPI()
    app.include_router(router, prefix="/api")
    from src.app.api.rate_limit import limiter

    app.state.limiter = limiter
    app.dependency_overrides[get_current_institution_admin] = lambda: user or _user()
    app.dependency_overrides[get_db_session_dep] = lambda: session
    return TestClient(app)


BASE = "/api/institution/enquiry-sources"


class TestIssuing:
    def test_the_token_is_returned_once_at_creation(self):
        session = _session()
        session._queue.append("loc-1")  # location ownership check
        r = _client(session).post(
            BASE, json={"label": "Website form", "location_id": "loc-1"}
        )
        assert r.status_code == 201
        data = r.json()
        assert data["token"]
        assert data["intake_url"].endswith(data["token"])

    def test_the_stored_row_holds_only_a_hash(self):
        """A credential recoverable from a backup is a live endpoint."""
        session = _session()
        session._queue.append("loc-1")
        r = _client(session).post(
            BASE, json={"label": "Website form", "location_id": "loc-1"}
        )
        stored = session.add.call_args[0][0]
        assert stored.token_hash == hash_intake_token(r.json()["token"])
        assert r.json()["token"] not in (stored.token_hash or "")

    def test_a_location_from_another_clinic_is_refused(self):
        """A token pointing elsewhere would land leads in the wrong tenant."""
        session = _session()
        session._queue.append(None)  # ownership check finds nothing
        r = _client(session).post(
            BASE, json={"label": "x", "location_id": "loc-other"}
        )
        assert r.status_code == 404
        session.add.assert_not_called()

    def test_a_source_without_a_location_skips_the_check(self):
        """A single-location practice should not be made to choose."""
        session = _session()
        r = _client(session).post(BASE, json={"label": "Website form"})
        assert r.status_code == 201
        assert r.json()["location_id"] is None

    def test_a_signing_secret_is_stored_encrypted(self):
        session = _session()
        _client(session).post(BASE, json={"label": "x", "signing_secret": "s3cret"})
        stored = session.add.call_args[0][0]
        assert stored.signing_secret == "s3cret"
        assert "s3cret" not in (stored.signing_secret_encrypted or "")

    def test_an_empty_label_is_refused(self):
        session = _session()
        r = _client(session).post(BASE, json={"label": ""})
        assert r.status_code == 422

    def test_a_user_with_no_institution_is_refused(self):
        session = _session()
        r = _client(session, user=_user(institution_id=None)).post(
            BASE, json={"label": "x"}
        )
        assert r.status_code == 400


class TestListing:
    def test_the_list_never_carries_a_token(self):
        session = _session(row=_row())
        r = _client(session).get(BASE)
        assert r.status_code == 200
        rows = r.json()
        assert rows
        assert "token" not in rows[0]
        assert "token_hash" not in rows[0]

    def test_it_reports_whether_a_secret_is_set_without_revealing_it(self):
        row = _row()
        row.signing_secret = "s3cret"
        rendered = _to_response(row)
        assert rendered.has_signing_secret is True
        assert "s3cret" not in str(rendered.model_dump())


class TestRevokeAndRotate:
    def test_revoking_switches_it_off_without_deleting(self):
        """A clinic that kills the wrong form should not have to reconfigure
        the provider to get it back."""
        row = _row()
        session = _session()
        session._queue.append(row)
        r = _client(session).patch(f"{BASE}/src-1", json={"is_active": False})
        assert r.status_code == 200
        assert r.json()["is_active"] is False
        assert row.token_hash  # unchanged, so reactivating restores the form

    def test_rotating_replaces_the_hash(self):
        row = _row()
        before = row.token_hash
        session = _session()
        session._queue.append(row)
        r = _client(session).post(f"{BASE}/src-1/rotate")
        assert r.status_code == 200
        assert row.token_hash != before
        assert row.token_hash == hash_intake_token(r.json()["token"])

    def test_another_clinics_source_is_not_found(self):
        session = _session()
        session._queue.append(None)
        r = _client(session).patch(f"{BASE}/src-999", json={"label": "mine now"})
        assert r.status_code == 404
