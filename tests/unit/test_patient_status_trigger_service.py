"""Unit tests for patient status workflow trigger helpers."""

from __future__ import annotations

from types import SimpleNamespace

from src.app.services.automation.patient_status_trigger_service import (
    patient_status_idempotency_key,
    workflow_matches_patient_status,
)


def test_workflow_matches_patient_status_trigger_status() -> None:
    workflow = SimpleNamespace(
        definition={
            "trigger": {
                "type": "patient_status_changed",
                "statuses": ["appointment_confirmed"],
            },
            "entry_node_id": "exit-1",
            "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
        },
    )

    assert workflow_matches_patient_status(workflow, "appointment_confirmed") is True
    assert workflow_matches_patient_status(workflow, "needs_followup") is False


def test_patient_status_idempotency_key_is_stable() -> None:
    assert (
        patient_status_idempotency_key("version-1", "event-1")
        == "patient-status:version-1:event-1"
    )
