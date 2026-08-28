"""Contract tests for the authoritative workflow node registry."""

from src.app.services.automation.node_registry import (
    NODE_CAPABILITIES,
    NODE_REGISTRY_VERSION,
    outgoing_references,
    public_capabilities,
)


EXPECTED_NODE_TYPES = {
    "wait",
    "wait_for_sms_reply",
    "drip",
    "send_sms",
    "retell_sms_conversation",
    "send_voice",
    "send_email",
    "update_patient_status",
    "update_appointment",
    "update_gotracker_appointment",
    "json_mapper",
    "llm",
    "condition",
    "switch",
    "exit",
}


def test_registry_declares_every_engine_node_as_runtime_and_dry_run_supported() -> None:
    assert NODE_REGISTRY_VERSION == "1.0"
    assert set(NODE_CAPABILITIES) == EXPECTED_NODE_TYPES
    assert all(capability.runtime_supported for capability in NODE_CAPABILITIES.values())
    assert all(capability.dry_run_supported for capability in NODE_CAPABILITIES.values())


def test_only_legacy_wait_is_not_authorable() -> None:
    hidden = {
        capability.node_type
        for capability in NODE_CAPABILITIES.values()
        if not capability.authorable
    }
    assert hidden == {"wait_for_sms_reply"}


def test_registry_owns_linear_and_condition_edges() -> None:
    assert outgoing_references(
        {"type": "update_appointment", "next_node_id": "exit-1"}
    ) == (("next_node_id", "exit-1"),)
    assert outgoing_references(
        {
            "type": "condition",
            "true_next_node_id": "yes",
            "false_next_node_id": "no",
        }
    ) == (("true_next_node_id", "yes"), ("false_next_node_id", "no"))
    assert outgoing_references({"type": "exit"}) == ()


def test_public_registry_is_json_ready() -> None:
    rows = public_capabilities()
    assert rows[0]["node_type"] == "wait"
    assert isinstance(rows[0]["outgoing_fields"], list)
