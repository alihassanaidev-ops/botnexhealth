"""Checks that catch a broken campaign before it runs.

Two failures this covers: a mistyped condition field, which used to resolve to
null and take the false branch forever with no error anywhere; and the hidden
allowlist that silently stripped recall campaigns of their fields.
"""

from __future__ import annotations

from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.definition_service import derive_pms_context_fields
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


# NOTE: the rendering tests that used to sit here were removed when staging's
# renderer won the merge. Staging renders permissively and records which fields
# came out blank, rather than refusing to send; `render_body`, dotted tokens and
# `{{field | "fallback"}}` are not part of that approach.
#
# Still open on staging as a result: a dotted token such as
# `{{appointment.status}}` is neither substituted nor stripped, so it reaches the
# patient verbatim — while the condition editor offers exactly those dotted
# names. Worth closing separately.

# --- publish-time validation -------------------------------------------------


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
