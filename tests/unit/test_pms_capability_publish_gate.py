"""Item 30 · a campaign the clinic's software cannot support must not publish.

Instantiating from a template already refused. The gap was that the requirement
never travelled into the definition, so publishing never re-checked it — and a
clinic can change practice software after the campaign was built, while a
workflow assembled without a template was never checked at all.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.validation_service import WorkflowValidationService


def _definition(
    requirements: list[str] | None = None,
    *,
    context_fields: list[str] | None = None,
) -> WorkflowDefinition:
    raw = {
        "entry_node_id": "done",
        "trigger": {"type": "manual"},
        "nodes": [{"id": "done", "type": "exit", "outcome": "done"}],
    }
    if requirements is not None:
        raw["pms_capability_requirements"] = requirements
    if context_fields is not None:
        raw["pms_context_fields"] = context_fields
    return WorkflowDefinition.model_validate(raw)


def _issues(definition, evaluation, *, location_id="loc-1"):
    session = AsyncMock()
    session.get = AsyncMock(return_value=MagicMock())
    service = WorkflowValidationService(session)
    with patch(
        "src.app.services.automation.pms_capability_service.PmsCapabilityService"
    ) as MockSvc:
        MockSvc.return_value.evaluate_location = AsyncMock(return_value=evaluation)
        return asyncio.run(
            service._pms_capability_issues(
                definition, institution_id="inst-1", location_id=location_id
            )
        )


def _evaluation(
    *, supported, missing=(), partial=(), unknown=(), message=""
) -> MagicMock:
    return MagicMock(
        supported=supported,
        missing=list(missing),
        partial=list(partial),
        unknown=list(unknown),
        message=message,
    )


class TestCarriedThroughInstantiation:
    def test_requirements_survive_into_the_definition(self):
        """Unlike the compliance block, these must reach the published version."""
        definition = _definition(["patient_recalls"])
        assert definition.pms_capability_requirements == ["patient_recalls"]

    def test_a_template_carries_its_requirements(self):
        from src.app.services.automation.campaign_templates import (
            _ALL_TEMPLATES,
            instantiate_definition,
        )

        template = next(
            t for t in _ALL_TEMPLATES.values() if t.metadata.pms_capability_requirements
        )
        built = instantiate_definition(template)
        assert built["pms_capability_requirements"] == list(
            template.metadata.pms_capability_requirements
        )


class TestPublishGate:
    def test_a_missing_capability_blocks_publishing(self):
        issues = _issues(
            _definition(["treatment_plans"]),
            _evaluation(supported=False, missing=["treatment_plans"]),
        )
        assert [i.severity for i in issues] == ["error"]
        assert not WorkflowValidationService.is_publishable(issues)

    def test_an_unknown_capability_is_treated_as_unavailable(self):
        """Guessing in the clinic's favour publishes a campaign that does nothing."""
        issues = _issues(
            _definition(["treatment_plans"]),
            _evaluation(supported=False, unknown=["treatment_plans"]),
        )
        assert issues and issues[0].severity == "error"

    def test_a_partial_capability_blocks_too(self):
        issues = _issues(
            _definition(["procedures"]),
            _evaluation(supported=False, partial=["procedures"]),
        )
        assert issues and issues[0].severity == "error"

    def test_pms_context_fields_derive_publish_requirements(self):
        issues = _issues(
            _definition(context_fields=["has_active_treatment_plan"]),
            _evaluation(supported=False, missing=["treatment_plans"]),
        )
        assert issues and issues[0].severity == "error"
        assert "treatment_plans" in issues[0].message

    def test_the_failure_names_the_capability_and_what_to_do(self):
        issues = _issues(
            _definition(["treatment_plans"]),
            _evaluation(supported=False, missing=["treatment_plans"]),
        )
        assert "treatment_plans" in issues[0].message
        assert issues[0].fix

    def test_a_supported_clinic_publishes_unchanged(self):
        assert (
            _issues(_definition(["patient_recalls"]), _evaluation(supported=True)) == []
        )


class TestScopeOfTheCheck:
    def test_a_campaign_declaring_nothing_is_unaffected(self):
        assert _issues(_definition([]), _evaluation(supported=False)) == []

    def test_template_level_validation_has_no_location_to_check(self):
        """Institution/template context: no location, so nothing to evaluate."""
        assert (
            _issues(
                _definition(["treatment_plans"]),
                _evaluation(supported=False, missing=["treatment_plans"]),
                location_id=None,
            )
            == []
        )
