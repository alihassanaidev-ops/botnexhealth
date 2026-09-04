"""A message with a hole in it must not reach a patient.

The previous behaviour had three ways to fail silently: an unknown token
rendered as empty string, a dotted token was emitted verbatim into the body, and
nothing at all validated a condition's field path. Each produced a campaign that
looked like it ran correctly.
"""

from __future__ import annotations

from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.definition_service import derive_pms_context_fields
from src.app.services.automation.template_renderer import render_body
from src.app.services.automation.validation_service import WorkflowValidationService

CONTEXT = {
    "appointment": {"status": "cancelled", "start_at": "2026-09-04T14:15:00"},
    "patient": {"first_name": "Jordan"},
    "patient_first_name": "Jordan",
}


def _definition(body: str, *, field: str = "appointment.status") -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "event", "event_keys": ["appointment.cancelled"]},
            "entry_node_id": "c1",
            "nodes": [
                {
                    "id": "c1",
                    "type": "condition",
                    "filter": {"kind": "rule", "field": field, "op": "eq", "value": "x"},
                    "true_next_node_id": "s1",
                    "false_next_node_id": "x1",
                },
                {
                    "id": "s1",
                    "type": "send_sms",
                    "body_template": body,
                    "next_node_id": "x1",
                },
                {"id": "x1", "type": "exit", "outcome": "done"},
            ],
        }
    )


def _codes(definition: WorkflowDefinition) -> list[str]:
    issues = WorkflowValidationService._merge_field_issues(definition)
    issues += WorkflowValidationService._condition_field_issues(definition)
    return [issue.code for issue in issues]


# --- rendering ---------------------------------------------------------------


def test_a_canonical_token_renders_instead_of_reaching_the_patient() -> None:
    """`{{appointment.status}}` used to be sent literally, braces and all."""
    result = render_body("Your appt is {{appointment.status}}", None, None, CONTEXT)

    assert result.text == "Your appt is cancelled"
    assert "{{" not in result.text


def test_a_missing_value_is_reported_rather_than_silently_blanked() -> None:
    result = render_body("Time for your {{recall.type}} visit", None, None, CONTEXT)

    assert result.unresolved == ["recall.type"]
    assert not result.complete


def test_a_fallback_makes_a_missing_value_safe() -> None:
    result = render_body(
        'Time for your {{recall.type | "check-up"}} visit', None, None, CONTEXT
    )

    assert result.text == "Time for your check-up visit"
    assert result.complete


def test_a_derived_merge_field_still_wins_over_a_context_path() -> None:
    """Published templates expect the catalog's formatting, not a raw value."""
    result = render_body("Hi {{patient_first_name}}", None, None, CONTEXT)
    assert result.text == "Hi Jordan"


# --- publish-time validation -------------------------------------------------


def test_publishing_a_token_the_trigger_cannot_supply_is_an_error() -> None:
    codes = _codes(_definition("Hi {{no_such_field}}"))
    assert "merge_field_unknown" in codes


def test_a_fallback_makes_the_same_token_publishable() -> None:
    codes = _codes(_definition('Hi {{no_such_field | "there"}}'))
    assert "merge_field_unknown" not in codes


def test_a_canonical_token_the_trigger_carries_is_accepted() -> None:
    codes = _codes(_definition("Your appt is {{appointment.status}}"))
    assert "merge_field_unknown" not in codes


def test_a_mistyped_condition_field_is_reported() -> None:
    """Previously this took the false branch forever with no signal at all."""
    codes = _codes(_definition("Hi {{patient_first_name}}", field="appointment.nope"))
    assert "condition_field_unavailable" in codes


def test_a_real_condition_field_is_accepted() -> None:
    codes = _codes(_definition("Hi {{patient_first_name}}", field="appointment.status"))
    assert "condition_field_unavailable" not in codes


def test_raw_pms_paths_stay_usable_as_the_escape_hatch() -> None:
    codes = _codes(_definition("Hi {{patient_first_name}}", field="raw.ChairFlowState"))
    assert "condition_field_unavailable" not in codes


# --- the silent strip --------------------------------------------------------


def test_recall_facts_are_derived_from_what_the_campaign_references() -> None:
    """A builder-authored recall campaign used to lose these entirely.

    `pms_context_fields` defaults to empty and no UI ever set it, so the fields
    were fetched only for templates that hardcoded the list.
    """
    definition = {
        "nodes": [
            {
                "type": "send_sms",
                "body_template": "Time for your {{recall_type_name}} visit",
            }
        ]
    }
    assert derive_pms_context_fields(definition) == ["recall_type_name"]


def test_a_condition_on_a_pms_fact_also_requests_it() -> None:
    definition = {"nodes": [{"filter": {"field": "has_active_treatment_plan"}}]}
    assert derive_pms_context_fields(definition) == ["has_active_treatment_plan"]


def test_an_explicitly_declared_field_is_never_dropped() -> None:
    """A published definition must not lose a fact it already asked for."""
    definition = {"pms_context_fields": ["last_visit_date"], "nodes": []}
    assert "last_visit_date" in derive_pms_context_fields(definition)


def test_a_campaign_that_needs_no_pms_facts_asks_for_none() -> None:
    assert derive_pms_context_fields({"nodes": [{"body_template": "Hi"}]}) == []
