"""PHI-safe snapshots for workflow step execution traces."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any


SAFE_TRACE_KEYS = {
    "appointment_at",
    "appointment_date",
    "appointment_datetime",
    "appointment_id",
    "appointment_location",
    "appointment_start_time",
    "appointment_status",
    "appointment_time",
    "appointment_type",
    "appointment_type_id",
    "appointment_type_name",
    "attempt_number",
    "batch_number",
    "batch_position",
    "batch_size",
    "booking_link",
    "branch",
    "branch_taken",
    "call_outcome",
    "campaign_goal",
    "completed_at",
    "confirmation_link",
    "currency",
    "direction",
    "disconnection_reason",
    "due_at",
    "due_local_at",
    "duration_ms",
    "external_ref",
    "fired_at",
    "interval_seconds",
    "location_id",
    "location_name",
    "max_attempts",
    "next_node_id",
    "operatory_id",
    "operatory_name",
    "outcome",
    "patient_status",
    "patient_workflow_status",
    "provider",
    "provider_id",
    "provider_message_id",
    "provider_name",
    "qa_reason",
    "recall_due_date",
    "recall_type",
    "reschedule_link",
    "result_code",
    "retell_agent_configured",
    "retell_agent_source",
    "retell_call_id",
    "retell_from_number_masked",
    "retell_from_number_normalized",
    "retell_from_number_source",
    "scheduled_at",
    "scheduled_local_at",
    "scheduled_timezone",
    "source",
    "source_patient_status_event_id",
    "source_workflow_id",
    "source_workflow_run_id",
    "source_workflow_step_id",
    "status",
    "status_written",
    "timezone",
    "to_number_masked",
    "to_number_normalized",
    "trigger_ref_id",
    "trigger_ref_type",
    "trigger_type",
    "voice_profile_id",
    "voice_profile_name",
}


def trace_safe_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-compatible mapping with unknown/PHI fields redacted."""
    return {
        str(key): trace_safe_value(str(key), value)
        for key, value in mapping.items()
        if value is not None
    }


def trace_safe_value(key: str, value: Any) -> Any:
    normalized = key.lower().replace("-", "_")
    if normalized not in SAFE_TRACE_KEYS:
        return "[redacted]"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [trace_safe_value(key, item) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(child_key): trace_safe_value(str(child_key), child_value)
            for child_key, child_value in value.items()
            if child_value is not None
        }
    return str(value)
