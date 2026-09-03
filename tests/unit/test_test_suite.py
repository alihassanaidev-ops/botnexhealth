"""The Test Suite: a keyed, non-production door onto the agent functions.

It can read PHI and, when explicitly allowed, write into a live practice, so
most of what is asserted here is about it refusing to do things.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.api.routes import test_suite as ts
from src.app.config import Settings, settings
from src.app.retell.functions import _function_registry
from src.app.retell.idempotency import IDEMPOTENT_FUNCTIONS

KEY = "test-suite-key-value"
BASE = "/api/v1/test-suite"


# ── Mounting: the gate that keeps this out of production ──────────────


class TestItIsNotMountedWhereItShouldNotBe:
    @staticmethod
    def _enabled(app_env: str, key: str | None) -> bool:
        return Settings.test_suite_enabled.fget(
            Settings.model_construct(app_env=app_env, test_suite_api_key=key)
        )

    def test_no_key_means_no_suite(self):
        assert self._enabled("staging", None) is False

    def test_a_key_in_production_still_means_no_suite(self):
        """Two independent conditions. Setting the key in prod must not be enough."""
        for env in ("production", "prod", "PRODUCTION"):
            assert self._enabled(env, KEY) is False, env

    def test_a_key_outside_production_enables_it(self):
        for env in ("staging", "local", "dev"):
            assert self._enabled(env, KEY) is True, env

    def test_the_router_is_absent_from_the_real_app_by_default(self):
        """The suite is off in this test environment, so the paths must not exist."""
        from src.app.main import app as real_app

        paths = {getattr(r, "path", "") for r in real_app.routes}
        assert not any(p.startswith(BASE) for p in paths)


# ── Auth ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "test_suite_api_key", KEY, raising=False)
    monkeypatch.setattr(settings, "app_env", "staging", raising=False)
    monkeypatch.setattr(settings, "test_suite_allow_writes", False, raising=False)
    app = FastAPI()
    app.state.limiter = __import__(
        "src.app.api.rate_limit", fromlist=["limiter"]
    ).limiter
    app.include_router(ts.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def _hdr(key: str = KEY) -> dict[str, str]:
    return {"X-Test-Suite-Key": key}


class TestAuth:
    def test_no_key_is_refused(self, client):
        assert client.get(f"{BASE}/functions").status_code == 401

    def test_a_wrong_key_is_refused(self, client):
        assert client.get(f"{BASE}/functions", headers=_hdr("nope")).status_code == 401

    def test_the_right_key_is_admitted(self, client):
        assert client.get(f"{BASE}/functions", headers=_hdr()).status_code == 200

    def test_a_wrong_key_and_a_missing_key_are_indistinguishable(self, client):
        """So the endpoint cannot be used to probe how close a guess is."""
        missing = client.get(f"{BASE}/functions")
        wrong = client.get(f"{BASE}/functions", headers=_hdr("nope"))
        assert missing.status_code == wrong.status_code
        assert missing.json() == wrong.json()


# ── Discovery ─────────────────────────────────────────────────────────


class TestDiscovery:
    def test_it_lists_every_registered_function(self, client):
        body = client.get(f"{BASE}/functions", headers=_hdr()).json()
        assert body["count"] == len(_function_registry)
        assert {f["name"] for f in body["functions"]} == set(_function_registry)

    def test_writing_functions_are_flagged(self, client):
        body = client.get(f"{BASE}/functions", headers=_hdr()).json()
        flagged = {f["name"] for f in body["functions"] if f["mutating"]}
        assert flagged == set(IDEMPOTENT_FUNCTIONS)
        assert "book_appointment" in flagged
        assert "lookup_patient" not in flagged

    def test_writers_are_not_callable_when_writes_are_off(self, client):
        body = client.get(f"{BASE}/functions", headers=_hdr()).json()
        by_name = {f["name"]: f for f in body["functions"]}
        assert by_name["book_appointment"]["callable_now"] is False
        assert by_name["find_appointment_slots"]["callable_now"] is True

    def test_health_says_what_it_will_not_do(self, client):
        body = client.get(f"{BASE}/health", headers=_hdr()).json()
        assert body["ok"] is True
        assert body["writes_allowed"] is False
        assert body["environment"] == "staging"


def test_the_mutating_list_cannot_drift_from_the_idempotency_registry():
    """One list, not two.

    A new function that writes gets an idempotency guard; deriving from that
    registry means it is automatically refused here too, instead of relying on
    somebody remembering a second list exists.
    """
    assert ts.MUTATING_FUNCTIONS == frozenset(IDEMPOTENT_FUNCTIONS)


# ── Refusing to write ─────────────────────────────────────────────────


class TestWriteSafety:
    @pytest.mark.parametrize("name", sorted(IDEMPOTENT_FUNCTIONS))
    def test_a_writer_is_refused_when_the_deployment_forbids_writes(self, client, name):
        r = client.post(
            f"{BASE}/functions/{name}",
            headers=_hdr(),
            json={"location": "any", "allow_writes": True},
        )
        assert r.status_code == 403
        assert "writes into the practice" in r.json()["detail"]

    def test_a_writer_is_refused_when_the_request_does_not_opt_in(
        self, client, monkeypatch
    ):
        """Deployment permission alone is not enough — both are required."""
        monkeypatch.setattr(settings, "test_suite_allow_writes", True, raising=False)
        r = client.post(
            f"{BASE}/functions/book_appointment",
            headers=_hdr(),
            json={"location": "any"},
        )
        assert r.status_code == 403

    def test_a_reader_needs_no_opt_in(self, client):
        """It gets as far as routing, which is the next check, not 403."""
        r = client.post(
            f"{BASE}/functions/lookup_patient", headers=_hdr(), json={"args": {}}
        )
        assert r.status_code == 400  # no location/agent_id supplied
        assert "location" in r.json()["detail"]


# ── Dispatch ──────────────────────────────────────────────────────────


TARGET = ts.TargetResponse(
    agent_id="agent_x",
    institution="Riverside Dental",
    institution_slug="riverside",
    location="Downtown",
    location_slug="downtown",
    pms="gotracker",
    timezone="America/Toronto",
    resolved=True,
)


class TestCallingAFunction:
    def test_an_unknown_function_says_where_to_look(self, client):
        r = client.post(
            f"{BASE}/functions/not_a_function", headers=_hdr(), json={"args": {}}
        )
        assert r.status_code == 404
        assert "/test-suite/functions" in r.json()["detail"]

    def test_it_runs_the_registered_handler_and_returns_its_result(self, client):
        captured = {}

        async def fake(args):
            from src.app.retell.functions import _call_context_var

            captured["ctx"] = dict(_call_context_var.get())
            captured["args"] = args
            return {"providers": ["Lisa"]}

        with (
            patch.dict(_function_registry, {"list_providers": fake}),
            patch.object(ts, "_resolve_target", AsyncMock(return_value=TARGET)),
            patch.object(ts, "log_audit", AsyncMock()),
        ):
            r = client.post(
                f"{BASE}/functions/list_providers",
                headers=_hdr(),
                json={"location": "downtown", "args": {"x": 1}},
            )

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["result"] == {"providers": ["Lisa"]}
        assert body["mutating"] is False
        assert body["target"]["location_slug"] == "downtown"
        assert body["duration_ms"] >= 0
        # The handler ran with production call context, which is the whole point.
        assert captured["args"] == {"x": 1}
        assert captured["ctx"]["agent_id"] == "agent_x"
        assert captured["ctx"]["call_id"].startswith("test-suite-")

    def test_a_raising_handler_is_reported_not_a_500(self, client):
        """The exception is the thing you came to see. Don't bury it in a 500."""

        async def boom(args):
            raise ValueError("no provider configured")

        with (
            patch.dict(_function_registry, {"list_providers": boom}),
            patch.object(ts, "_resolve_target", AsyncMock(return_value=TARGET)),
            patch.object(ts, "log_audit", AsyncMock()),
        ):
            r = client.post(
                f"{BASE}/functions/list_providers",
                headers=_hdr(),
                json={"location": "downtown"},
            )

        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "ValueError: no provider configured"

    def test_an_in_band_error_is_not_reported_as_success(self, client):
        """Handlers signal failure by returning {"error": ...} as often as by raising."""

        async def refused(args):
            return {"error": "Could not resolve institution + location"}

        with (
            patch.dict(_function_registry, {"list_providers": refused}),
            patch.object(ts, "_resolve_target", AsyncMock(return_value=TARGET)),
            patch.object(ts, "log_audit", AsyncMock()),
        ):
            r = client.post(
                f"{BASE}/functions/list_providers",
                headers=_hdr(),
                json={"location": "downtown"},
            )

        assert r.json()["ok"] is False

    def test_the_call_context_is_cleared_afterwards(self, client):
        from src.app.retell.functions import _call_context_var

        async def fake(args):
            return {}

        with (
            patch.dict(_function_registry, {"list_providers": fake}),
            patch.object(ts, "_resolve_target", AsyncMock(return_value=TARGET)),
            patch.object(ts, "log_audit", AsyncMock()),
        ):
            client.post(
                f"{BASE}/functions/list_providers",
                headers=_hdr(),
                json={"location": "downtown"},
            )

        assert _call_context_var.get().get("agent_id") is None

    def test_every_call_is_audited(self, client):
        async def fake(args):
            return {}

        audit = AsyncMock()
        with (
            patch.dict(_function_registry, {"list_providers": fake}),
            patch.object(ts, "_resolve_target", AsyncMock(return_value=TARGET)),
            patch.object(ts, "log_audit", audit),
        ):
            client.post(
                f"{BASE}/functions/list_providers",
                headers=_hdr(),
                json={"location": "downtown", "args": {"secret_arg": "value"}},
            )

        audit.assert_awaited_once()
        meta = audit.await_args.kwargs["metadata"]
        assert meta["source"] == "test_suite"
        assert meta["function"] == "list_providers"
        assert meta["location_slug"] == "downtown"
        # Argument *names* are useful; argument values may be PHI.
        assert meta["arg_keys"] == ["secret_arg"]
        assert "value" not in str(meta)


class TestRouting:
    def test_neither_location_nor_agent_id_is_a_clear_400(self, client):
        r = client.post(
            f"{BASE}/functions/list_providers", headers=_hdr(), json={"args": {}}
        )
        assert r.status_code == 400
        assert "agent_id" in r.json()["detail"]


class TestAmbiguousSlugs:
    """Location slugs are unique per institution, not globally.

    Two practices can each have a "main". Picking the first match would run the
    test against the wrong clinic and report success, which is worse than
    failing.
    """

    @staticmethod
    def _session_with(locations, institutions):
        from unittest.mock import MagicMock

        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = locations
        result.scalars.return_value.first.return_value = (
            locations[0] if locations else None
        )
        session.execute = AsyncMock(return_value=result)
        session.get = AsyncMock(side_effect=lambda _m, pk: institutions.get(str(pk)))

        class _Ctx:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                return False

        return _Ctx()

    @staticmethod
    def _loc(loc_id, inst_id, slug="main"):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=loc_id,
            institution_id=inst_id,
            slug=slug,
            name=slug.title(),
            timezone="UTC",
            retell_agent_id=f"agent-{loc_id}",
        )

    @staticmethod
    def _inst(slug):
        from types import SimpleNamespace

        return SimpleNamespace(name=slug.title(), slug=slug, pms_type="nexhealth")

    def test_a_slug_in_two_institutions_is_refused_not_guessed(self, client):
        locs = [self._loc("l1", "i1"), self._loc("l2", "i2")]
        insts = {"i1": self._inst("alpha-dental"), "i2": self._inst("beta-dental")}
        with patch.object(
            ts, "get_system_db_session", lambda *_a, **_k: self._session_with(locs, insts)
        ):
            r = client.post(
                f"{BASE}/functions/list_providers",
                headers=_hdr(),
                json={"location": "main"},
            )
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "alpha-dental" in detail and "beta-dental" in detail
        assert "institution" in detail

    def test_naming_the_institution_resolves_it(self, client):
        locs = [self._loc("l1", "i1"), self._loc("l2", "i2")]
        insts = {"i1": self._inst("alpha-dental"), "i2": self._inst("beta-dental")}

        async def fake(args):
            return {"ok": 1}

        with (
            patch.object(
                ts,
                "get_system_db_session",
                lambda *_a, **_k: self._session_with(locs, insts),
            ),
            patch.dict(_function_registry, {"list_providers": fake}),
            patch.object(ts, "log_audit", AsyncMock()),
        ):
            r = client.post(
                f"{BASE}/functions/list_providers",
                headers=_hdr(),
                json={"location": "main", "institution": "beta-dental"},
            )
        assert r.status_code == 200
        assert r.json()["target"]["institution_slug"] == "beta-dental"

    def test_a_location_with_no_agent_bound_says_so(self, client):
        """Real staging data has one: slug 'main', no Retell agent."""
        loc = self._loc("l1", "i1")
        loc.retell_agent_id = None
        with patch.object(
            ts,
            "get_system_db_session",
            lambda *_a, **_k: self._session_with([loc], {"i1": self._inst("alpha")}),
        ):
            r = client.post(
                f"{BASE}/functions/list_providers",
                headers=_hdr(),
                json={"location": "main"},
            )
        assert r.status_code == 409
        assert "no Retell agent bound" in r.json()["detail"]
