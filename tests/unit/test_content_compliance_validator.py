"""Unit tests for the Plan 12 content-class + PHI compliance validator."""

from __future__ import annotations

import asyncio

from src.app.services.automation.content_compliance_validator import ContentComplianceValidator
from src.app.services.automation.definition_schema import WorkflowDefinition


def _definition(*, content_class, sms_body="Hi {first_name}, please confirm your visit."):
    return WorkflowDefinition.model_validate(
        {
            "schema_version": "1.0",
            "trigger": {"type": "manual"},
            "entry_node_id": "n1",
            "compliance": {"content_class": content_class, "consent_required": True},
            "nodes": [
                {
                    "id": "n1",
                    "type": "send_sms",
                    "body_template": sms_body,
                    "next_node_id": "n2",
                },
                {"id": "n2", "type": "exit", "outcome": "done"},
            ],
        }
    )


def _validate(definition):
    return asyncio.run(
        ContentComplianceValidator().validate(
            definition, institution_id="inst-1", location_id="loc-1"
        )
    )


def test_clean_reminder_has_no_issues():
    issues = _validate(_definition(content_class="transactional_care"))
    assert issues == []


def test_promotional_language_in_exempt_class_is_error():
    issues = _validate(
        _definition(
            content_class="transactional_care",
            sms_body="Book now and get 20% off — limited time whitening special!",
        )
    )
    codes = {(i.code, i.severity, i.node_id) for i in issues}
    assert ("promotional_in_exempt_class", "error", "n1") in codes


def test_promotional_language_allowed_in_marketing_class():
    issues = _validate(
        _definition(
            content_class="sales",
            sms_body="Book now and get 20% off — limited time whitening special!",
        )
    )
    # No promotional-in-exempt error for a sales campaign (it carries its own
    # express-consent requirement, enforced by the consent-path guardrail).
    assert not any(i.code == "promotional_in_exempt_class" for i in issues)


def test_high_risk_phi_in_body_is_error():
    issues = _validate(
        _definition(
            content_class="transactional_care",
            sms_body="Your biopsy diagnosis is ready and your balance due is $200.",
        )
    )
    phi = [i for i in issues if i.code == "phi_in_body"]
    assert phi and phi[0].severity == "error" and phi[0].node_id == "n1"


def test_sensitive_clinical_term_is_warning():
    issues = _validate(
        _definition(
            content_class="transactional_care",
            sms_body="Reminder for your root canal appointment tomorrow.",
        )
    )
    sensitive = [i for i in issues if i.code == "sensitive_clinical_in_body"]
    assert sensitive and sensitive[0].severity == "warning"


def test_recall_class_is_also_exempt():
    issues = _validate(
        _definition(
            content_class="recall",
            sms_body="Special offer: coupon for your overdue cleaning!",
        )
    )
    assert any(i.code == "promotional_in_exempt_class" and i.severity == "error" for i in issues)


def _voice_definition(content_class):
    return WorkflowDefinition.model_validate(
        {
            "schema_version": "1.0",
            "trigger": {"type": "manual"},
            "entry_node_id": "v1",
            "compliance": {"content_class": content_class, "consent_required": True},
            "nodes": [
                {
                    "id": "v1",
                    "type": "send_voice",
                    "retell_agent_id": "agent_1",
                    "next_node_id": "n2",
                },
                {"id": "n2", "type": "exit", "outcome": "done"},
            ],
        }
    )


def test_voice_node_requires_disclosure_warning():
    issues = _validate(_voice_definition("transactional_care"))
    codes = {i.code for i in issues}
    assert "ai_voice_disclosure_required" in codes
    # non-marketing class does not add the express-consent warning
    assert "ai_voice_marketing_needs_express_consent" not in codes


def test_marketing_voice_needs_express_consent_warning():
    issues = _validate(_voice_definition("sales"))
    codes = {i.code for i in issues}
    assert "ai_voice_disclosure_required" in codes
    assert "ai_voice_marketing_needs_express_consent" in codes


def test_word_boundary_avoids_false_positive():
    # "sale" substring in "wholesale"/"salesperson" must not trip promotional.
    issues = _validate(
        _definition(
            content_class="transactional_care",
            sms_body="Our salesperson will not call; this is only an appointment reminder.",
        )
    )
    assert not any(i.code == "promotional_in_exempt_class" for i in issues)
