"""The two sweeps that stop a form integration failing silently.

A lead-form integration does not break loudly. It stops producing leads, and the
practice finds out weeks later. Both sweeps exist for that, so the tests are
about the resume points and the classification being right — get either wrong
and the sweep runs happily while leads keep disappearing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.services.forms.connection_service import upsert_connection
from src.app.services.forms.providers.base import ProviderAccount
from src.app.tasks.form_integrations import (
    RECONCILE_LOOKBACK_HOURS,
    RECONCILE_OVERLAP_MINUTES,
    _since_for,
)


# ── reconciliation resume point ─────────────────────────────────────────
def test_a_form_with_no_history_looks_back_a_bounded_window() -> None:
    """A first run on an established form must not import months of history."""
    since = _since_for(None)
    expected = datetime.now(timezone.utc) - timedelta(hours=RECONCILE_LOOKBACK_HOURS)
    assert abs((since - expected).total_seconds()) < 5


def test_the_resume_point_overlaps_the_last_submission() -> None:
    """Provider clocks and ours differ; a lead landing exactly on the boundary
    would be skipped without the overlap, and skipped means lost."""
    last = datetime.now(timezone.utc) - timedelta(hours=3)
    since = _since_for(last)
    assert since == last - timedelta(minutes=RECONCILE_OVERLAP_MINUTES)


def test_a_naive_timestamp_is_treated_as_utc() -> None:
    """Postgres can hand back a naive datetime; comparing it against an aware
    'now' would raise and abort the sweep for that form."""
    last = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    assert _since_for(last).tzinfo is not None


def test_the_resume_point_never_runs_ahead_of_now() -> None:
    future = datetime.now(timezone.utc) + timedelta(days=2)
    assert _since_for(future) <= datetime.now(timezone.utc) + timedelta(seconds=1)


# ── reconnecting a disconnected account ─────────────────────────────────
@pytest.mark.asyncio
async def test_reconnecting_revives_the_existing_connection() -> None:
    """Otherwise the forms, field maps and every landed submission are stranded
    behind a dead row while a second row starts empty."""
    existing = SimpleNamespace(
        id="conn-1",
        institution_id="inst-1",
        provider="meta",
        account_ref="page-1",
        account_name="Clinic Page",
        status="revoked",
        disconnected_at=datetime.now(timezone.utc),
        last_error="This connection expired.",
        token_expires_at=None,
        granted_scopes=None,
        access_token=None,
        refresh_token=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()

    row = await upsert_connection(
        session,
        institution_id="inst-1",
        provider="meta",
        account=ProviderAccount(
            account_ref="page-1",
            account_name="Clinic Page",
            access_token="fresh-token",
        ),
        user_id="user-1",
    )

    assert row is existing
    assert row.disconnected_at is None
    assert row.status == "active"
    assert row.last_error is None
    # No second row: the history hangs off this id.
    session.add.assert_not_called()
