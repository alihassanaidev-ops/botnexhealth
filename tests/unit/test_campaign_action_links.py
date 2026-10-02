"""Item 12 · signed, expiring per-run booking / confirm / reschedule links.

The three placeholders existed and templates used them, but nothing ever
produced a value, so a message using one reached the patient with the link
missing. These links are opened from a text message with no login, so the token
is the only thing standing between a stranger and a clinic's schedule.
"""

from __future__ import annotations

import pytest

from src.app.services.automation.campaign_action_links import (
    ACTIONS,
    DEFAULT_TTL_SECONDS,
    EXPIRED,
    INVALID,
    LINK_RESPONSE_HEADERS,
    AUTO_PLACEHOLDER_ACTIONS,
    PLACEHOLDER_ACTIONS,
    build_run_links,
    make_action_token,
    verify_action_token,
)

NOW = 1_800_000_000


class TestRoundTrip:
    @pytest.mark.parametrize("action", ACTIONS)
    def test_a_fresh_token_verifies_to_its_run_and_action(self, action):
        token = make_action_token("run-1", action, now=NOW)
        assert verify_action_token(token, now=NOW + 60) == ("run-1", action)

    def test_the_token_carries_no_patient_identifier(self):
        token = make_action_token("run-1", "book", now=NOW)
        assert "patient" not in token
        # run id, action, expiry, signature — nothing else.
        assert len(token.split(".")) == 4


class TestTampering:
    def test_a_forged_signature_is_rejected(self):
        token = make_action_token("run-1", "book", now=NOW)
        run, action, expiry, _sig = token.split(".")
        forged = f"{run}.{action}.{expiry}.{'0' * 32}"
        assert verify_action_token(forged, now=NOW + 60) == INVALID

    def test_swapping_the_run_is_rejected(self):
        """Otherwise a patient could act on another patient's run."""
        token = make_action_token("run-1", "book", now=NOW)
        _run, action, expiry, sig = token.split(".")
        assert verify_action_token(f"run-2.{action}.{expiry}.{sig}", now=NOW + 60) == INVALID

    def test_a_confirm_link_cannot_be_edited_into_a_reschedule(self):
        """Purpose separation: the action is inside the signed payload."""
        token = make_action_token("run-1", "confirm", now=NOW)
        run, _action, expiry, sig = token.split(".")
        assert verify_action_token(f"{run}.reschedule.{expiry}.{sig}", now=NOW + 60) == INVALID

    def test_pushing_the_expiry_forward_is_rejected(self):
        """The signature covers the expiry, so it cannot simply be edited."""
        token = make_action_token("run-1", "book", ttl_seconds=60, now=NOW)
        run, action, expiry, sig = token.split(".")
        extended = f"{run}.{action}.{int(expiry) + 10_000_000}.{sig}"
        assert verify_action_token(extended, now=NOW + 3600) == INVALID

    @pytest.mark.parametrize(
        "token", [None, "", "junk", "a.b.c", "a.b.c.d.e", "run.book.notanumber.sig"]
    )
    def test_malformed_tokens_are_rejected(self, token):
        assert verify_action_token(token, now=NOW) == INVALID

    def test_an_unknown_action_is_rejected(self):
        assert verify_action_token(f"run-1.delete.{NOW + 99}.x" * 1, now=NOW) == INVALID

    def test_make_refuses_an_unknown_action(self):
        with pytest.raises(ValueError):
            make_action_token("run-1", "delete", now=NOW)  # type: ignore[arg-type]


class TestExpiry:
    def test_an_expired_token_is_distinguishable_from_a_forged_one(self):
        """A patient whose link ran out can be told so; a forger cannot."""
        token = make_action_token("run-1", "book", ttl_seconds=60, now=NOW)
        assert verify_action_token(token, now=NOW + 61) == EXPIRED

    def test_expiry_is_exclusive_at_the_boundary(self):
        token = make_action_token("run-1", "book", ttl_seconds=60, now=NOW)
        assert verify_action_token(token, now=NOW + 60) == EXPIRED
        assert verify_action_token(token, now=NOW + 59) == ("run-1", "book")

    def test_default_ttl_outlives_a_campaign_run(self):
        """A reminder ladder can span a fortnight; the link must outlive it."""
        assert DEFAULT_TTL_SECONDS >= 14 * 24 * 3600

    def test_a_link_is_reusable_until_it_expires(self):
        """Not single-use: a patient may open it, get interrupted, come back."""
        token = make_action_token("run-1", "book", now=NOW)
        assert verify_action_token(token, now=NOW + 100) == ("run-1", "book")
        assert verify_action_token(token, now=NOW + 200) == ("run-1", "book")


class TestRunLinks:
    def test_all_three_placeholders_are_produced(self):
        """The three every run gets. registration_link is issued by its own
        step instead, so it is deliberately not in here."""
        links = build_run_links("run-1", "https://app.example.com", now=NOW)
        assert set(links) == set(AUTO_PLACEHOLDER_ACTIONS)

    def test_each_link_verifies_to_its_own_action(self):
        links = build_run_links("run-1", "https://app.example.com", now=NOW)
        for placeholder, url in links.items():
            token = url.split("token=")[1]
            assert verify_action_token(token, now=NOW + 60) == (
                "run-1",
                PLACEHOLDER_ACTIONS[placeholder],
            )

    def test_base_url_trailing_slash_does_not_double(self):
        links = build_run_links("run-1", "https://app.example.com/", now=NOW)
        # booking now lands on the page, so check the path it actually uses.
        assert "//book/" not in links["booking_link"].replace("https://", "")
        assert "//api/" not in links["confirmation_link"]


def test_referrer_policy_is_no_referrer():
    """Booking references in URLs leak to third parties through the Referer
    header — the documented failure mode across hotel booking sites."""
    assert LINK_RESPONSE_HEADERS["Referrer-Policy"] == "no-referrer"
    assert LINK_RESPONSE_HEADERS["Cache-Control"] == "no-store"
