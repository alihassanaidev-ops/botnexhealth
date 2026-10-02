"""{{registration_link}} resolves only when a step actually issues one.

The booking placeholders are generated for every run. This one is not: a link
that creates records in a clinic's practice software should exist only for a
campaign that asked for one. That makes it possible to use the placeholder in a
message with nothing producing a value — the exact failure these placeholders
had before, where the message went out with the link missing — so publish
validation refuses it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.services.automation.campaign_action_links import REGISTRATION_PLACEHOLDER
from src.app.services.automation.definition_schema import (
    PatientRegistrationNode,
    SendSmsNode,
)
from src.app.services.automation.validation_service import (
    WorkflowValidationService,
)


def _sms(node_id: str, body: str) -> SendSmsNode:
    # Real nodes, not fakes: the rule reads templates through isinstance checks,
    # so a stand-in would make the test pass while the rule saw nothing.
    return SendSmsNode(id=node_id, body_template=body, next_node_id="x")


def _registration_step(node_id: str = "reg-1") -> PatientRegistrationNode:
    return PatientRegistrationNode(id=node_id, provider_id="77", next_node_id="x")


def _definition(nodes):
    return SimpleNamespace(nodes=nodes, entry_node_id=nodes[0].id if nodes else "")


def _issues(definition):
    return WorkflowValidationService._registration_link_issues(definition)


class TestValidation:
    def test_using_the_link_without_a_step_is_an_error(self):
        issues = _issues(
            _definition([_sms("m1", "Register here: {{registration_link}}")])
        )
        assert len(issues) == 1
        assert issues[0].code == "registration_link_not_issued"
        assert issues[0].severity == "error"
        assert issues[0].node_id == "m1"

    def test_the_error_says_how_to_fix_it(self):
        issues = _issues(
            _definition([_sms("m1", "Register here: {{registration_link}}")])
        )
        assert "Register Patient step" in issues[0].fix

    def test_a_step_in_the_definition_makes_it_valid(self):
        issues = _issues(
            _definition(
                [
                    _registration_step(),
                    _sms("m1", "Register here: {{registration_link}}"),
                ]
            )
        )
        assert issues == []

    def test_a_message_without_the_placeholder_is_untouched(self):
        issues = _issues(_definition([_sms("m1", "See you Tuesday.")]))
        assert issues == []

    def test_the_booking_placeholder_is_not_affected(self):
        """Those are generated for every run and need no step."""
        issues = _issues(_definition([_sms("m1", "Book: {{booking_link}}")]))
        assert issues == []

    def test_every_offending_message_is_reported_not_just_the_first(self):
        issues = _issues(
            _definition(
                [
                    _sms("m1", "{{registration_link}}"),
                    _sms("m2", "{{registration_link}}"),
                ]
            )
        )
        assert {issue.node_id for issue in issues} == {"m1", "m2"}


class TestPlaceholderName:
    def test_the_placeholder_is_what_the_docs_and_catalog_call_it(self):
        assert REGISTRATION_PLACEHOLDER == "registration_link"
