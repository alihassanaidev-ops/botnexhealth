"""Outbound campaign calls end in the outcome their recall routes branch on."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.app.services.automation.campaign_templates import get_template
from src.app.services.automation.voice_outcome import campaign_call_outcome


def _outcome(reason, tags=None, **custom):
    if tags is not None:
        custom["Call Status"] = tags
    return campaign_call_outcome(reason, "ended", custom)


@pytest.mark.parametrize(
    ("reason", "outcome"),
    [
        ("dial_no_answer", "no_answer"),
        ("voicemail_reached", "voicemail"),
        ("dial_busy", "busy"),
        ("inactivity", "no_answer"),  # silence: retry like a missed call
        ("dial_failed", "failed"),
        ("invalid_destination", "failed"),
        ("network_blocked", "failed"),
        ("call_transfer", "transferred"),
        ("transfer_bridged", "transferred"),
        ("transfer_cancelled", "callback_requested"),  # transfer failed
        ("user_requested_dnc", "do_not_call"),
        ("user_declined", "declined"),
    ],
)
def test_calls_that_did_not_reach_a_conversation(reason, outcome):
    assert _outcome(reason) == outcome


@pytest.mark.parametrize(
    ("tags", "outcome"),
    [
        ("Appointment Booked", "booked"),
        ("Appointment Rescheduled", "booked"),
        ("Needs Call Back", "callback_requested"),
        ("Needs Callback", "callback_requested"),
        ("Need Callback", "callback_requested"),
        ("Transferred", "transferred"),
        ("Complaint", "staff_handoff"),
        ("Financial Inquiry", "staff_handoff"),
        ("Insurance and Billing", "staff_handoff"),
        ("No Action Needed", "contacted"),
        ("FAQ Handled", "contacted"),
        # The strongest tag wins when the agent sets several.
        ("FAQ Handled, Appointment Booked", "booked"),
        ("No Action Needed, Needs Call Back", "callback_requested"),
    ],
)
def test_answered_calls_follow_the_call_status_tags(tags, outcome):
    assert _outcome("user_hangup", tags) == outcome
    assert _outcome("agent_hangup", tags) == outcome


def test_answered_call_without_tags_stays_answered():
    assert _outcome("user_hangup") == "answered"
    assert _outcome("user_hangup", "something new") == "answered"


def test_spoken_opt_out_wins_over_a_booking():
    with patch(
        "src.app.services.automation.voice_optout_service.settings.retell_optout_analysis_key",
        "do_not_call",
    ):
        assert _outcome("user_hangup", "Appointment Booked", do_not_call=True) == "do_not_call"
        assert _outcome("user_hangup", "Appointment Booked", do_not_call=False) == "booked"


def test_recall_agent_do_not_call_field_name_is_honored():
    with patch(
        "src.app.services.automation.voice_optout_service.settings.retell_optout_analysis_key",
        "Do not call",
    ):
        custom = {"Do not call": True, "Call Status": "no action needed"}
        assert campaign_call_outcome("agent_hangup", "ended", custom) == "do_not_call"


def test_explicit_call_outcome_field_still_wins_over_tags():
    assert _outcome("user_hangup", "No Action Needed", call_outcome="booked") == "booked"


def test_recall_routes_cover_every_outcome():
    nodes = get_template("recall-voice-6month").definition["nodes"]
    route = next(n for n in nodes if n["id"] == "route-recall-attempt-1")
    exits = {n["id"]: n["outcome"] for n in nodes if n["type"] == "exit"}

    def route_for(outcome):
        for case in route["cases"]:
            if outcome in case["filter"]["value"]:
                return case["next_node_id"]
        return route["default_next_node_id"]

    assert exits[route_for("booked")] == "booked"
    assert exits[route_for("callback_requested")] == "callback_requested"
    assert exits[route_for("do_not_call")] == "do_not_contact"
    assert exits[route_for("transferred")] == "transferred"
    assert exits[route_for("contacted")] == "contacted"
    assert exits[route_for("staff_handoff")] == "staff_handoff"
    assert exits[route_for("failed")] == "staff_handoff"
    # Missed calls retry: attempt 1 routes to the wait before attempt 2.
    assert route_for("no_answer") == "wait-recall-retry-1"
