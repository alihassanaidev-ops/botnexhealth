"""The public form-intake endpoint.

Deliberately weighted to what it refuses. This is the one route in the campaign
system that an arbitrary third party posts to, so the interesting cases are the
malformed, the unauthorised and the abusive ones — the happy path is two lines.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.api.routes.enquiry_intake import MAX_BODY_BYTES, router
from src.app.models.enquiry_intake_source import (
    generate_intake_token,
    hash_intake_token,
)

TOKEN = generate_intake_token()
SECRET = "s3cret-signing-key"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    # slowapi's decorator needs limiter state on the app.
    from src.app.api.rate_limit import limiter

    app.state.limiter = limiter
    return TestClient(app)


def _source(active=True, secret=None, location="loc-1", defaults=None):
    source = MagicMock()
    source.id = "src-1"
    source.institution_id = "inst-1"
    source.location_id = location
    source.source_name = "typeform"
    source.default_attribution = defaults
    source.signing_secret = secret
    source.is_active = active
    source.token_hash = hash_intake_token(TOKEN)
    return source


def _session(source):
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = source
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=source)
    session.flush = AsyncMock()
    return session


_DEFAULT = object()


def _post(client, body, *, source=_DEFAULT, token=TOKEN, headers=None, intake=None):
    # A sentinel, because `source=None` is a meaningful case here — it is the
    # unknown-token path — and must not be confused with "use the default".
    session = _session(_source() if source is _DEFAULT else source)

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    with patch(
        "src.app.api.routes.enquiry_intake.get_system_db_session", return_value=_Ctx()
    ), patch(
        "src.app.api.routes.enquiry_intake.intake_enquiry",
        intake or AsyncMock(return_value=MagicMock()),
    ) as spy:
        response = client.post(
            f"/api/enquiries/intake/{token}",
            content=json.dumps(body) if isinstance(body, (dict, list)) else body,
            headers={"content-type": "application/json", **(headers or {})},
        )
    return response, spy


class TestCredential:
    def test_an_unknown_token_is_refused(self, client):
        r, _ = _post(client, {"email": "a@b.com"}, source=None)
        assert r.status_code == 401

    def test_a_revoked_source_is_refused(self, client):
        r, _ = _post(client, {"email": "a@b.com"}, source=_source(active=False))
        assert r.status_code == 401

    def test_unknown_and_revoked_are_indistinguishable(self, client):
        """Otherwise the endpoint maps which tokens exist."""
        unknown, _ = _post(client, {"email": "a@b.com"}, source=None)
        revoked, _ = _post(client, {"email": "a@b.com"}, source=_source(active=False))
        assert unknown.status_code == revoked.status_code
        assert unknown.json() == revoked.json()

    def test_the_token_is_never_echoed(self, client):
        r, _ = _post(client, {"email": "a@b.com"}, source=None)
        assert TOKEN not in r.text

    def test_a_short_token_never_reaches_the_database(self, client):
        r = client.post("/api/enquiries/intake/tiny", content="{}")
        assert r.status_code == 422


class TestSignature:
    def _signed(self, body: dict) -> tuple[str, str]:
        raw = json.dumps(body)
        digest = hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
        return raw, digest

    def test_a_source_with_a_secret_requires_a_signature(self, client):
        r, _ = _post(client, {"email": "a@b.com"}, source=_source(secret=SECRET))
        assert r.status_code == 401

    def test_a_correct_signature_is_accepted(self, client):
        raw, digest = self._signed({"email": "a@b.com"})
        r, _ = _post(
            client, raw, source=_source(secret=SECRET),
            headers={"X-Signature": digest},
        )
        assert r.status_code == 202

    def test_the_sha256_prefix_form_is_accepted(self, client):
        raw, digest = self._signed({"email": "a@b.com"})
        r, _ = _post(
            client, raw, source=_source(secret=SECRET),
            headers={"X-Signature": f"sha256={digest}"},
        )
        assert r.status_code == 202

    def test_a_tampered_body_fails_even_with_the_old_signature(self, client):
        """The point of signing: the URL token cannot prove the body."""
        _, digest = self._signed({"email": "a@b.com"})
        r, _ = _post(
            client, json.dumps({"email": "attacker@evil.com"}),
            source=_source(secret=SECRET), headers={"X-Signature": digest},
        )
        assert r.status_code == 401

    def test_a_source_without_a_secret_does_not_require_one(self, client):
        r, _ = _post(client, {"email": "a@b.com"}, source=_source(secret=None))
        assert r.status_code == 202


class TestPayload:
    def test_a_lead_with_no_way_to_reach_them_is_refused(self, client):
        r, spy = _post(client, {"first_name": "Dana"})
        assert r.status_code == 422
        assert r.json()["error"] == "no_contact_method"
        spy.assert_not_awaited()

    def test_an_oversized_body_is_refused_before_parsing(self, client):
        r, spy = _post(client, "x" * (MAX_BODY_BYTES + 10))
        assert r.status_code == 413
        spy.assert_not_awaited()

    def test_malformed_json_is_refused(self, client):
        r, spy = _post(client, "{not json")
        assert r.status_code == 422
        spy.assert_not_awaited()

    def test_unknown_fields_are_tolerated(self, client):
        """Forms post far more than they were asked for."""
        r, _ = _post(
            client, {"email": "a@b.com", "utm_term": "x", "hidden": {"a": 1}}
        )
        assert r.status_code == 202

    def test_a_single_word_name_does_not_invent_a_surname(self, client):
        r, spy = _post(client, {"email": "a@b.com", "name": "Cher"})
        kwargs = spy.await_args.kwargs
        assert kwargs["first_name"] == "Cher"
        assert kwargs["last_name"] is None

    def test_a_multi_word_surname_is_kept_whole(self, client):
        _, spy = _post(client, {"email": "a@b.com", "name": "Ana Maria de Souza"})
        kwargs = spy.await_args.kwargs
        assert kwargs["first_name"] == "Ana"
        assert kwargs["last_name"] == "Maria de Souza"


class TestHostedFormAnswers:
    def test_email_and_phone_are_read_from_the_answers_list(self, client):
        _, spy = _post(client, {
            "answers": [
                {"type": "email", "email": "dana@example.com"},
                {"type": "phone_number", "phone_number": "+15054821234"},
                {"type": "short_text", "text": "Dana Reyes",
                 "field": {"ref": "full_name"}},
            ]
        })
        kwargs = spy.await_args.kwargs
        assert kwargs["email"] == "dana@example.com"
        assert kwargs["phone"] == "+15054821234"
        assert kwargs["first_name"] == "Dana"

    def test_a_phone_typed_into_a_text_box_is_not_treated_as_a_phone(self, client):
        """Reading the declared type, not the shape of the value: we will not
        text somebody on the strength of a guess."""
        r, spy = _post(client, {
            "answers": [{"type": "short_text", "text": "505-482-1234",
                         "field": {"ref": "anything"}}]
        })
        assert r.status_code == 422  # no contact method

    def test_top_level_fields_win_over_the_answers_list(self, client):
        _, spy = _post(client, {
            "email": "explicit@example.com",
            "answers": [{"type": "email", "email": "from-answers@example.com"}],
        })
        assert spy.await_args.kwargs["email"] == "explicit@example.com"

    def test_junk_entries_in_the_answers_list_do_not_crash_it(self, client):
        r, _ = _post(client, {
            "email": "a@b.com",
            "answers": ["not-a-dict", None, {"type": "email"}, {}],
        })
        assert r.status_code == 202


class TestConsent:
    def test_nothing_is_claimed_when_the_form_did_not_ask(self, client):
        _, spy = _post(client, {"phone": "5054821234"})
        assert spy.await_args.kwargs["consent_channels"] == ()

    def test_only_the_declared_channels_are_passed_on(self, client):
        _, spy = _post(client, {
            "phone": "5054821234", "email": "a@b.com",
            "consent_sms": True, "consent_email": False,
        })
        assert spy.await_args.kwargs["consent_channels"] == ("sms",)

    def test_the_wording_travels_with_it(self, client):
        _, spy = _post(client, {
            "phone": "5054821234", "consent_sms": True,
            "consent_wording": "Yes, text me",
        })
        assert spy.await_args.kwargs["consent_wording"] == "Yes, text me"


class TestIdempotencyAndAttribution:
    def test_a_supplied_key_is_used(self, client):
        _, spy = _post(client, {"email": "a@b.com", "intake_key": "resp-77"})
        assert spy.await_args.kwargs["intake_key"] == "resp-77"

    def test_a_missing_key_is_derived_and_stable(self, client):
        """A provider that sends no id must still not duplicate on retry."""
        _, first = _post(client, {"email": "a@b.com"})
        _, second = _post(client, {"email": "a@b.com"})
        assert (
            first.await_args.kwargs["intake_key"]
            == second.await_args.kwargs["intake_key"]
        )

    def test_different_people_derive_different_keys(self, client):
        _, a = _post(client, {"email": "a@b.com"})
        _, b = _post(client, {"email": "c@d.com"})
        assert a.await_args.kwargs["intake_key"] != b.await_args.kwargs["intake_key"]

    def test_source_defaults_merge_under_the_request(self, client):
        _, spy = _post(
            client,
            {"email": "a@b.com", "attribution": {"utm_source": "request"}},
            source=_source(defaults={"utm_source": "default", "form": "contact"}),
        )
        attribution = spy.await_args.kwargs["attribution"]
        assert attribution["utm_source"] == "request"  # request wins
        assert attribution["form"] == "contact"        # default still there


class TestFailureDisclosure:
    def test_the_reply_does_not_say_whether_the_lead_was_already_known(self, client):
        """An open endpoint that distinguishes them reveals a clinic's patients."""
        new, _ = _post(client, {"email": "a@b.com"},
                       intake=AsyncMock(return_value=MagicMock(created=True)))
        seen, _ = _post(client, {"email": "a@b.com"},
                        intake=AsyncMock(return_value=MagicMock(created=False)))
        assert new.json() == seen.json()
        assert new.status_code == seen.status_code

    def test_an_internal_failure_never_echoes_the_submitted_values(self, client):
        r, _ = _post(
            client, {"email": "dana@example.com", "phone": "5054821234"},
            intake=AsyncMock(side_effect=RuntimeError(
                "duplicate key for dana@example.com / 5054821234"
            )),
        )
        assert r.status_code == 503
        assert "dana@example.com" not in r.text
        assert "5054821234" not in r.text
