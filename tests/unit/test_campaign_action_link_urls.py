"""Where an action link actually sends the patient, and who gets one.

Two defects motivate this file. Every link resolved to the API even after the
patient-facing pages were built, so a booking link handed the patient to staff
instead of showing the slot picker. And registration had no placeholder at all,
so the form it opens was unreachable from any campaign message.
"""

from __future__ import annotations

import pytest

from src.app.services.automation.campaign_action_links import (
    AUTO_PLACEHOLDER_ACTIONS,
    PAGE_ACTIONS,
    PLACEHOLDER_ACTIONS,
    REGISTRATION_PLACEHOLDER,
    build_run_links,
    registration_link,
    verify_action_token,
)

BASE = "https://staging.example.com"


class TestWhereLinksLand:
    @pytest.mark.parametrize("placeholder", ["booking_link", "reschedule_link"])
    def test_page_backed_links_open_the_page_not_the_api(self, placeholder):
        url = build_run_links("run-1", BASE)[placeholder]
        assert "/api/" not in url, "a booking link that hits the API hands the patient to staff"
        assert url.startswith(f"{BASE}/book/")

    def test_confirm_stays_on_the_api(self):
        """One tap, nothing to decide — the endpoint performs the write-back."""
        url = build_run_links("run-1", BASE)["confirmation_link"]
        assert url.startswith(f"{BASE}/api/campaigns/link/confirm")

    def test_registration_opens_the_form(self):
        assert registration_link("run-1", BASE).startswith(f"{BASE}/book/register")

    def test_every_page_action_has_a_page_route(self):
        """Mirrors the routes declared in router.tsx."""
        assert PAGE_ACTIONS == {"book", "reschedule", "cancel", "register"}

    def test_a_trailing_slash_on_the_base_does_not_double_up(self):
        url = build_run_links("run-1", BASE + "/")["booking_link"]
        assert "//book/" not in url.replace("https://", "")


class TestWhoGetsALink:
    def test_every_run_gets_the_three_booking_links(self):
        assert set(build_run_links("run-1", BASE)) == set(AUTO_PLACEHOLDER_ACTIONS)

    def test_registration_is_not_handed_out_automatically(self):
        """A link that creates patient records must not exist for every run."""
        assert REGISTRATION_PLACEHOLDER not in build_run_links("run-1", BASE)

    def test_but_validation_still_knows_it_is_an_action_link(self):
        """So a message using it is checked against the public base URL."""
        assert REGISTRATION_PLACEHOLDER in PLACEHOLDER_ACTIONS
        assert PLACEHOLDER_ACTIONS[REGISTRATION_PLACEHOLDER] == "register"


class TestTokensStayPurposeScoped:
    def test_a_registration_token_verifies_as_register(self):
        url = registration_link("run-1", BASE)
        token = url.split("token=")[1]
        assert verify_action_token(token) == ("run-1", "register")

    def test_a_booking_token_is_not_a_registration_token(self):
        token = build_run_links("run-1", BASE)["booking_link"].split("token=")[1]
        run_id, action = verify_action_token(token)
        assert action == "book" and run_id == "run-1"
