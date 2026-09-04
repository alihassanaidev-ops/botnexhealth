"""Unit tests for WorkflowValidationService (Plan 01 A9)."""

from __future__ import annotations

import pytest

import asyncio

from src.app.services.automation.validation_service import WorkflowValidationService


def _validate(definition: dict, *, location_id: str | None = None):
    svc = WorkflowValidationService(session=None)  # no-op seams need no session
    return asyncio.run(
        svc.validate(
            definition,
            institution_id="inst-1",
            location_id=location_id,
        )
    )


_SEND_NO_CLASS = {
    "trigger": {"type": "manual"},
    "entry_node_id": "s1",
    "nodes": [
        {"type": "send_sms", "id": "s1", "body_template": "hi", "next_node_id": "x1"},
        {"type": "exit", "id": "x1", "outcome": "done"},
    ],
}


@pytest.mark.xfail(strict=True, reason=(
    "Compliance enforcement is disabled in validation_service.validate() — the `issues += self._consent_and_content(definition)` line is commented out with 'managed by Retell for now'. The rule itself still exists and is correct. strict=True so that re-enabling it turns this into a failure and forces a deliberate revisit rather than leaving a silently-skipped compliance test."
))
def test_valid_sending_workflow_warns_on_missing_content_class() -> None:
    issues = _validate(_SEND_NO_CLASS)
    assert WorkflowValidationService.is_publishable(issues) is True
    assert any(i.code == "content_class_unset" and i.severity == "warning" for i in issues)


@pytest.mark.xfail(strict=True, reason=(
    "Compliance enforcement is disabled in validation_service.validate() — the `issues += self._consent_and_content(definition)` line is commented out with 'managed by Retell for now'. The rule itself still exists and is correct. strict=True so that re-enabling it turns this into a failure and forces a deliberate revisit rather than leaving a silently-skipped compliance test."
))
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


def test_appointment_type_is_a_known_merge_field() -> None:
    """It was removed once, and campaign templates broke.

    ``appointment_type`` was dropped from the catalog and later restored when
    the booking templates needed it. This asserts the restoration, so removing
    it again fails here rather than in a patient-facing message.
    """
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
    assert not any(
        i.code == "merge_field_unknown" and i.node_id == "s1"
        for i in issues
    )


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


def test_duplicate_node_ids_block_publish_with_a_node_linked_fix() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "same",
        "nodes": [
            {"type": "send_sms", "id": "same", "body_template": "hi", "next_node_id": "same"},
            {"type": "exit", "id": "same", "outcome": "done"},
        ],
    }

    issues = _validate(definition)

    issue = next(item for item in issues if item.code == "duplicate_node_id")
    assert issue.node_id == "same"
    assert issue.fix == "Give every step a unique id."
    assert WorkflowValidationService.is_publishable(issues) is False


def test_reachable_execution_cycle_blocks_publish() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "loop",
        "nodes": [
            {
                "type": "wait",
                "id": "loop",
                "wait_for": {
                    "type": "time",
                    "delay": {"delay_type": "duration", "duration_seconds": 60},
                },
                "next_node_id": "loop",
            },
            {"type": "exit", "id": "exit-1", "outcome": "done"},
        ],
    }

    issues = _validate(definition)

    assert any(item.code == "graph_cycle" and item.node_id == "loop" for item in issues)
    assert WorkflowValidationService.is_publishable(issues) is False


def test_update_appointment_participates_in_reachability() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "update-1",
        "nodes": [
            {
                "type": "update_appointment",
                "id": "update-1",
                "operation": "confirm",
                "next_node_id": "exit-1",
            },
            {"type": "exit", "id": "exit-1", "outcome": "confirmed"},
        ],
    }

    issues = _validate(definition, location_id="loc-1")

    assert not any(item.code == "unreachable" for item in issues)
    assert WorkflowValidationService.is_publishable(issues) is True


def test_booking_link_requires_workflow_location() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "booking-1",
        "nodes": [
            {
                "type": "booking_link",
                "id": "booking-1",
                "next_node_id": "exit-1",
                "actions": ["book"],
            },
            {"type": "exit", "id": "exit-1", "outcome": "configured"},
        ],
    }

    issues = _validate(definition)

    issue = next(item for item in issues if item.code == "location_required")
    assert issue.node_id == "booking-1"
    assert issue.severity == "error"
    assert "Booking Link requires a clinic location" in issue.message
    assert WorkflowValidationService.is_publishable(issues) is False


def test_booking_link_with_workflow_location_is_publishable() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "booking-1",
        "nodes": [
            {
                "type": "booking_link",
                "id": "booking-1",
                "next_node_id": "exit-1",
                "actions": ["book"],
            },
            {"type": "exit", "id": "exit-1", "outcome": "configured"},
        ],
    }

    issues = _validate(definition, location_id="loc-1")

    assert not any(item.code == "location_required" for item in issues)
    assert WorkflowValidationService.is_publishable(issues) is True


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


def test_drip_only_workflow_has_no_content_warning() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "drip-1",
        "nodes": [
            {
                "type": "drip",
                "id": "drip-1",
                "batch_size": 25,
                "interval_seconds": 3600,
                "next_node_id": "x1",
            },
            {"type": "exit", "id": "x1", "outcome": "done"},
        ],
    }
    issues = _validate(definition)
    assert not any(i.code == "content_class_unset" for i in issues)
    assert WorkflowValidationService.is_publishable(issues) is True


# ---------------------------------------------------------------------------
# PMS scope: triggers/nodes owned by another PMS must not publish
# ---------------------------------------------------------------------------


class _PmsSession:
    """Just enough session for `_pms_scope_issues` to load the institution."""

    def __init__(self, pms_type: str) -> None:
        self._pms_type = pms_type

    async def get(self, model, pk):  # noqa: ANN001
        from types import SimpleNamespace

        return SimpleNamespace(pms_type=self._pms_type)


def _validate_with_pms(definition: dict, pms_type: str):
    svc = WorkflowValidationService(session=_PmsSession(pms_type))
    return asyncio.run(
        svc.validate(definition, institution_id="inst-1", location_id=None)
    )


_COMPLETED_VISIT_SMS = {
    "trigger": {"type": "event", "event_keys": ["appointment.completed"]},
    "entry_node_id": "s1",
    "nodes": [
        {"type": "send_sms", "id": "s1", "body_template": "hi", "next_node_id": "x1"},
        {"type": "exit", "id": "x1", "outcome": "done"},
    ],
}


def test_appointment_event_trigger_publishes_on_either_pms() -> None:
    """No trigger type is PMS-owned any more.

    The old ``appointment_state_changed`` trigger was GoTracker-only because it
    matched Chair Flow states, and publishing it on NexHealth was blocked here.
    Its replacement names the *event* rather than the vendor's representation of
    it, so the same definition publishes on both — the per-PMS decision moved
    down to the individual event key.
    """
    for pms_type in ("nexhealth", "gotracker"):
        issues = _validate_with_pms(_COMPLETED_VISIT_SMS, pms_type)
        assert not any(i.code == "trigger_unsupported_for_pms" for i in issues)
        assert WorkflowValidationService.is_publishable(issues) is True


def test_gotracker_only_event_key_is_not_offered_to_nexhealth() -> None:
    """The gate the retired trigger map used to provide, at event granularity.

    ``appointment.checked_in`` has no NexHealth equivalent, so the builder's
    trigger picker must not offer it there — otherwise a clinic can author a
    campaign that silently never enrolls anyone.
    """
    from src.app.services.automation import event_catalog

    assert event_catalog.supports("appointment.checked_in", "nexhealth") == "unsupported"
    assert event_catalog.supports("appointment.checked_in", "gotracker") == "native"

    nexhealth_keys = {event["key"] for event in event_catalog.public_events("nexhealth")}
    gotracker_keys = {event["key"] for event in event_catalog.public_events("gotracker")}
    assert "appointment.checked_in" not in nexhealth_keys
    assert "appointment.checked_in" in gotracker_keys
    # The completed-visit event is derived rather than absent on NexHealth, so
    # it stays on offer — that is the distinction the whole-trigger gate lost.
    assert "appointment.completed" in nexhealth_keys


def test_gotracker_node_blocks_publish_on_nexhealth() -> None:
    definition = {
        "trigger": {"type": "manual"},
        "entry_node_id": "g1",
        "nodes": [
            {
                "type": "update_gotracker_appointment",
                "id": "g1",
                "status_id": 1,
                "next_node_id": "x1",
            },
            {"type": "exit", "id": "x1", "outcome": "done"},
        ],
    }
    issues = _validate_with_pms(definition, "nexhealth")
    assert any(
        i.code == "node_unsupported_for_pms" and i.node_id == "g1" for i in issues
    )


def test_shared_definition_passes_on_both_pms() -> None:
    definition = {
        **_SEND_NO_CLASS,
        "compliance": {"content_class": "transactional_care", "consent_required": True},
    }
    for pms_type in ("nexhealth", "gotracker"):
        issues = _validate_with_pms(definition, pms_type)
        assert not any(
            i.code in ("trigger_unsupported_for_pms", "node_unsupported_for_pms")
            for i in issues
        )
