"""Unit tests for WorkflowValidationService (Plan 01 A9)."""

from __future__ import annotations

import asyncio

from src.app.services.automation.validation_service import WorkflowValidationService


def _validate(definition: dict):
    svc = WorkflowValidationService(session=None)  # no-op seams need no session
    return asyncio.run(svc.validate(definition, institution_id="inst-1"))


_SEND_NO_CLASS = {
    "trigger": {"type": "manual"},
    "entry_node_id": "s1",
    "nodes": [
        {"type": "send_sms", "id": "s1", "body_template": "hi", "next_node_id": "x1"},
        {"type": "exit", "id": "x1", "outcome": "done"},
    ],
}


def test_valid_sending_workflow_warns_on_missing_content_class() -> None:
    issues = _validate(_SEND_NO_CLASS)
    assert WorkflowValidationService.is_publishable(issues) is True
    assert any(i.code == "content_class_unset" and i.severity == "warning" for i in issues)


def test_marketing_without_consent_is_a_publish_error() -> None:
    definition = {
        **_SEND_NO_CLASS,
        "compliance": {"content_class": "marketing", "consent_required": False},
    }
    issues = _validate(definition)
    assert WorkflowValidationService.is_publishable(issues) is False
    assert any(i.code == "consent_required" and i.severity == "error" for i in issues)


def test_transactional_with_consent_is_clean() -> None:
    definition = {
        **_SEND_NO_CLASS,
        "compliance": {"content_class": "transactional_care", "consent_required": True},
    }
    issues = _validate(definition)
    assert WorkflowValidationService.is_publishable(issues) is True
    assert not any(i.code == "content_class_unset" for i in issues)


def test_unknown_merge_field_warns() -> None:
    definition = {
        **_SEND_NO_CLASS,
        "nodes": [
            {
                "type": "send_sms",
                "id": "s1",
                "body_template": "Hi {{does_not_exist}}",
                "next_node_id": "x1",
            },
            {"type": "exit", "id": "x1", "outcome": "done"},
        ],
    }
    issues = _validate(definition)
    assert any(i.code == "merge_field_unknown" and i.node_id == "s1" for i in issues)


def test_merge_field_unavailable_for_trigger_warns() -> None:
    definition = {
        **_SEND_NO_CLASS,
        "trigger": {"type": "manual"},
        "nodes": [
            {
                "type": "send_sms",
                "id": "s1",
                "body_template": "Your appointment is {{appointment_date}}",
                "next_node_id": "x1",
            },
            {"type": "exit", "id": "x1", "outcome": "done"},
        ],
    }
    issues = _validate(definition)
    assert any(
        i.code == "merge_field_unavailable_for_trigger" and i.node_id == "s1"
        for i in issues
    )


def test_merge_field_unavailable_for_channel_warns() -> None:
    definition = {
        **_SEND_NO_CLASS,
        "trigger": {"type": "appointment_offset", "offset_hours": -24},
        "nodes": [
            {
                "type": "send_sms",
                "id": "s1",
                "body_template": "Type {{appointment_type}}",
                "next_node_id": "x1",
            },
            {"type": "exit", "id": "x1", "outcome": "done"},
        ],
    }
    issues = _validate(definition)
    assert any(
        i.code == "merge_field_unavailable_for_channel" and i.node_id == "s1"
        for i in issues
    )
    assert any(i.code == "merge_field_phi_warning" and i.node_id == "s1" for i in issues)


def test_unreachable_node_is_warned() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "s1",
        "compliance": {"content_class": "recall", "consent_required": True},
        "nodes": [
            {"type": "send_sms", "id": "s1", "body_template": "hi", "next_node_id": "x1"},
            {"type": "exit", "id": "x1", "outcome": "done"},
            # Orphan wait node not referenced by any edge.
            {
                "type": "wait",
                "id": "orphan",
                "delay": {"delay_type": "duration", "duration_seconds": 60},
                "next_node_id": "x1",
            },
        ],
    }
    issues = _validate(definition)
    assert any(
        i.code == "unreachable" and i.node_id == "orphan" and i.severity == "warning"
        for i in issues
    )


def test_missing_exit_is_structural_error_node_linked() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "s1",
        "nodes": [
            {"type": "send_sms", "id": "s1", "body_template": "hi", "next_node_id": "s1"},
        ],
    }
    issues = _validate(definition)
    assert WorkflowValidationService.is_publishable(issues) is False
    assert any("exit node" in i.message for i in issues)


def test_non_sending_workflow_has_no_content_warning() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "w1",
        "nodes": [
            {
                "type": "wait",
                "id": "w1",
                "delay": {"delay_type": "duration", "duration_seconds": 60},
                "next_node_id": "x1",
            },
            {"type": "exit", "id": "x1", "outcome": "done"},
        ],
    }
    issues = _validate(definition)
    assert not any(i.code == "content_class_unset" for i in issues)
    assert WorkflowValidationService.is_publishable(issues) is True
