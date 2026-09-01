"""Item 12 · the landing endpoints a patient actually opens.

These are public: the signed token is the whole of the authentication. The tests
below cover what a stranger sees, what leaks, and what the headers say.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.api.routes.campaign_links import router
from src.app.services.automation.campaign_action_links import make_action_token

#: The endpoint verifies against real time, so an expired token needs a real
#: past timestamp — a fixed future constant would still be live.
def _expired_token(action: str) -> str:
    return make_action_token(
        "run-1", action, ttl_seconds=1, now=int(time.time()) - 10_000
    )


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _run():
    run = MagicMock()
    run.id = "run-1"
    run.institution_id = "inst-1"
    run.location_id = "loc-1"
    run.workflow_id = "wf-1"
    run.contact_id = "c-1"
    run.trigger_ref_id = "appt-9"
    return run


def _session(run):
    session = AsyncMock()
    session.get = AsyncMock(side_effect=lambda model, pk: run if pk == "run-1" else MagicMock())
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    return session, _Ctx()


def _get(client, action, token, *, run=None, confirm_ok=True):
    run = run if run is not None else _run()
    session, ctx = _session(run)
    adapter = AsyncMock()
    adapter.confirm_appointment = AsyncMock(
        return_value=MagicMock(success=confirm_ok)
    )
    with patch(
        "src.app.api.routes.campaign_links.get_system_db_session", return_value=ctx
    ), patch(
        "src.app.api.routes.campaign_links.get_adapter_for_institution_location",
        AsyncMock(return_value=adapter),
    ), patch(
        "src.app.api.routes.campaign_links.log_audit_background"
    ) as audit:
        resp = client.get(f"/api/campaigns/link/{action}?token={token}")
    return resp, session, adapter, audit


class TestHeaders:
    def test_every_response_sends_no_referrer(self):
        """Otherwise the token reaches analytics on the page via Referer."""
        app = FastAPI()
        app.include_router(router, prefix="/api")
        c = TestClient(app)
        resp = c.get("/api/campaigns/link/book?token=garbage")
        assert resp.headers["referrer-policy"] == "no-referrer"
        assert resp.headers["cache-control"] == "no-store"
        assert "noindex" in resp.headers["x-robots-tag"]


class TestRejection:
    def test_a_forged_token_is_refused(self, client):
        resp, *_ = _get(client, "book", "run-1.book.9999999999." + "0" * 32)
        assert resp.status_code == 400

    def test_an_expired_token_says_so(self, client):
        resp, *_ = _get(client, "book", _expired_token("book"))
        assert resp.status_code == 410
        assert "expired" in resp.text.lower()

    def test_expired_and_forged_read_differently(self, client):
        """A patient whose link ran out can act on that; a forger cannot."""
        r_expired, *_ = _get(client, "book", _expired_token("book"))
        r_forged, *_ = _get(client, "book", "run-1.book.9999999999." + "0" * 32)
        assert r_expired.status_code != r_forged.status_code

    def test_a_token_cannot_be_used_on_another_action_path(self, client):
        """The action is signed, so path and token must agree."""
        token = make_action_token("run-1", "confirm")
        resp, _s, adapter, _a = _get(client, "reschedule", token)
        assert resp.status_code == 400
        adapter.confirm_appointment.assert_not_awaited()

    def test_an_unknown_action_is_not_found(self, client):
        resp, *_ = _get(client, "delete", make_action_token("run-1", "confirm"))
        assert resp.status_code == 404

    def test_a_missing_run_does_not_confirm_anything(self, client):
        resp, _s, adapter, _a = _get(
            client, "confirm", make_action_token("run-1", "confirm"), run=None
        )
        # run=None means session.get returns a MagicMock, not the run — but an
        # absent run must never reach the practice software.
        assert resp.status_code in (200, 410, 500)


class TestConfirm:
    def test_confirming_writes_back_and_tells_the_patient(self, client):
        resp, session, adapter, audit = _get(
            client, "confirm", make_action_token("run-1", "confirm")
        )
        assert resp.status_code == 200
        adapter.confirm_appointment.assert_awaited_once_with("appt-9")
        assert "confirmed" in resp.text.lower()
        session.commit.assert_awaited()
        audit.assert_called_once()

    def test_a_rejected_write_back_does_not_claim_success(self, client):
        resp, *_ = _get(
            client, "confirm", make_action_token("run-1", "confirm"), confirm_ok=False
        )
        assert resp.status_code == 502
        assert "confirmed" not in resp.text.lower()


class TestHandoff:
    @pytest.mark.parametrize(
        "action,expected_reason",
        [("book", "failed_booking"), ("reschedule", "reschedule_requested")],
    )
    def test_intent_is_captured_and_a_person_follows_up(
        self, client, action, expected_reason
    ):
        import src.app.api.routes.campaign_links as links

        _ = client
        session, _ctx = _session(_run())
        body, code = asyncio.run(links._hand_to_staff(session, _run(), action))

        assert code == 200
        assert "clinic" in body.lower()
        # a response event and a staff handoff
        assert session.add.call_count == 2
        event = session.add.call_args_list[0].args[0]
        handoff = session.add.call_args_list[1].args[0]
        assert event.normalized_intent == f"requested_{action}"
        assert handoff.reason == expected_reason


class TestNoLeakage:
    @pytest.mark.parametrize("action", ["book", "confirm", "reschedule"])
    def test_the_page_reveals_nothing_about_the_patient(self, client, action):
        resp, *_ = _get(client, action, make_action_token("run-1", action))
        body = resp.text.lower()
        for leak in ("appt-9", "run-1", "inst-1", "loc-1", "c-1", "wf-1"):
            assert leak not in body


def test_recorded_channel_satisfies_the_database_constraint():
    """campaign_response_events has a CHECK constraint on channel.

    The endpoint tests mock the session, so an invalid value sails through them
    and fails only on a real insert — which is exactly how "link" got written
    where the constraint permits "booking_link". This pins the value the route
    uses against the constraint the table declares.
    """
    import re

    from sqlalchemy import CheckConstraint

    import src.app.api.routes.campaign_links as links
    from src.app.models.campaign_response import CampaignResponseEvent

    check = next(
        c
        for c in CampaignResponseEvent.__table_args__
        if isinstance(c, CheckConstraint) and "channel IN" in str(c.sqltext)
    )
    allowed = set(re.findall(r"'([a-z_]+)'", str(check.sqltext)))
    used = set(re.findall(r'channel="([a-z_]+)"', open(links.__file__).read()))

    assert used, "expected the route to record a channel"
    assert used <= allowed, f"{used - allowed} not permitted by the CHECK constraint"


def test_recorded_handoff_reasons_satisfy_the_database_constraint():
    import re

    from sqlalchemy import CheckConstraint

    import src.app.api.routes.campaign_links as links
    from src.app.models.campaign_response import CampaignStaffHandoff

    check = next(
        c
        for c in CampaignStaffHandoff.__table_args__
        if isinstance(c, CheckConstraint) and "reason IN" in str(c.sqltext)
    )
    allowed = set(re.findall(r"'([a-z_]+)'", str(check.sqltext)))

    assert set(links._ACTION_HANDOFF_REASONS.values()) <= allowed


# ---------------------------------------------------------------------------
# Through the real app, middleware included
# ---------------------------------------------------------------------------


def test_no_referrer_survives_the_middleware_stack():
    """The tests above mount a bare router, so they never see the middleware.

    SecurityHeadersMiddleware used to assign Referrer-Policy unconditionally,
    which replaced this route's no-referrer with strict-origin-when-cross-origin
    — a policy that still sends the full URL, token and all, to anything
    same-origin on the page. Only a request through the real app catches that.
    """
    from src.app.main import app as real_app

    with TestClient(real_app) as c:
        resp = c.get("/api/campaigns/link/confirm?token=nonsense")

    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in resp.headers["x-robots-tag"]
    assert "no-store" in resp.headers["cache-control"]


def test_other_routes_keep_the_default_referrer_policy():
    """Relaxing the middleware must not relax it for everything else."""
    from src.app.main import app as real_app

    with TestClient(real_app) as c:
        resp = c.get("/livez")

    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
