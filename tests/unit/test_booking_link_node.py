"""The booking_link node's rules, and the API's enforcement of them.

The point of these tests is the distinction the feature exists for: the voice
agent's "new patients may only book these types" rule lives in its Retell
prompt, so it is guidance an LLM follows. Here the same restriction has to be a
property of the server, which means filtering the offered list is not enough —
booking a type that was never offered must be refused outright.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.api.routes.campaign_booking import (
    _action_permitted,
    _allowed_type_ids,
    _type_is_allowed,
    _window_days,
)
from src.app.services.automation.campaign_action_links import (
    BOOKING_LINK_CONFIG_KEY,
    REGISTRATION_CONFIG_KEY,
)
from src.app.services.automation.definition_schema import (
    BookingLinkNode,
    PatientRegistrationNode,
)


class _Run:
    """Only the field the helpers read."""

    def __init__(self, config: dict | None = None, key: str = BOOKING_LINK_CONFIG_KEY):
        self.trigger_metadata = {key: config} if config is not None else None


class TestNodeDefinition:
    def test_defaults_to_booking_only(self):
        node = BookingLinkNode(id="b1", next_node_id="n2")
        assert node.actions == ["book"]
        assert node.appointment_type_ids == []
        assert node.window_days == 7

    def test_duplicate_actions_are_rejected(self):
        with pytest.raises(ValidationError):
            BookingLinkNode(id="b1", next_node_id="n2", actions=["book", "book"])

    def test_at_least_one_action_is_required(self):
        with pytest.raises(ValidationError):
            BookingLinkNode(id="b1", next_node_id="n2", actions=[])

    @pytest.mark.parametrize("days", [0, 61])
    def test_the_window_is_bounded(self, days):
        """A link must not become an unbounded scan of the clinic's calendar."""
        with pytest.raises(ValidationError):
            BookingLinkNode(id="b1", next_node_id="n2", window_days=days)

    def test_registration_requires_a_provider(self):
        """provider_id is a clinic decision, not one a patient can supply."""
        with pytest.raises(ValidationError):
            PatientRegistrationNode(id="r1", next_node_id="n2")


class TestRestrictionIsOptIn:
    """A run with no config must behave exactly as it did before the node."""

    def test_no_metadata_allows_any_type(self):
        assert _type_is_allowed(_Run(), "12345") is True

    def test_empty_list_allows_any_type(self):
        assert _type_is_allowed(_Run({"appointment_type_ids": []}), "12345") is True

    def test_unrelated_metadata_is_ignored(self):
        run = _Run()
        run.trigger_metadata = {"appointment_type_id": "999"}
        assert _type_is_allowed(run, "12345") is True

    def test_a_non_dict_config_does_not_crash(self):
        run = _Run()
        run.trigger_metadata = {BOOKING_LINK_CONFIG_KEY: "corrupted"}
        assert _allowed_type_ids(run) == set()
        assert _type_is_allowed(run, "12345") is True


class TestTypeRestriction:
    def test_an_offered_type_is_allowed(self):
        run = _Run({"appointment_type_ids": ["12", "13"]})
        assert _type_is_allowed(run, "12") is True

    def test_a_type_outside_the_list_is_refused(self):
        run = _Run({"appointment_type_ids": ["12", "13"]})
        assert _type_is_allowed(run, "99") is False

    def test_omitting_the_type_is_refused_when_restricted(self):
        """Booking with no type must not slip past a restriction."""
        run = _Run({"appointment_type_ids": ["12"]})
        assert _type_is_allowed(run, None) is False

    def test_ids_compare_as_strings(self):
        """PMS ids arrive as ints in some payloads and strings in others."""
        run = _Run({"appointment_type_ids": [12, 13]})
        assert _type_is_allowed(run, "12") is True


class TestActionRestriction:
    def test_an_offered_action_is_permitted(self):
        assert _action_permitted(_Run({"actions": ["confirm", "reschedule"]}), "confirm")

    def test_an_action_the_node_never_offered_is_refused(self):
        """A reminder campaign offering confirm must not also hand out booking."""
        run = _Run({"actions": ["confirm", "reschedule"]})
        assert _action_permitted(run, "book") is False

    def test_no_config_permits_everything(self):
        assert _action_permitted(_Run(), "book") is True


class TestWindow:
    def test_the_configured_window_is_used(self):
        assert _window_days(_Run({"window_days": 21}), 7) == 21

    def test_a_missing_window_falls_back(self):
        assert _window_days(_Run({}), 7) == 7

    @pytest.mark.parametrize("bad", [0, -3, "14", None])
    def test_a_nonsense_window_falls_back(self, bad):
        assert _window_days(_Run({"window_days": bad}), 7) == 7


class TestConfigKeysAreDistinct:
    def test_booking_and_registration_do_not_share_a_key(self):
        assert BOOKING_LINK_CONFIG_KEY != REGISTRATION_CONFIG_KEY
