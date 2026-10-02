"""PHI-safe snapshots for workflow step execution traces.

Snapshots pass context through readably and redact only direct patient
identifiers (names, phone numbers, emails, birth dates, addresses, raw
payload blobs). Operational keys — event names, flags, external ids,
timestamps — are kept so an execution trace explains itself.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

#: Keys whose values are direct patient identifiers and never belong in a
#: step execution trace. Matched on the normalized (lowercase, underscored)
#: key. Masked variants ("to_number_masked") are intentionally not listed —
#: they exist to be shown.
PII_TRACE_KEYS = {
    "address",
    "address1",
    "address2",
    "birth_date",
    "birthdate",
    "body",
    "cell_phone",
    "contact_name",
    "date_of_birth",
    "dob",
    "email",
    "email_address",
    "first_name",
    "from_number",
    "full_name",
    "home_phone",
    "insurance_id",
    "insurance_member_id",
    "last_name",
    "message_body",
    "mobile",
    "mobile_phone",
    "name",
    "nickname",
    "patient_email",
    "patient_first_name",
    "patient_last_name",
    "patient_name",
    "patient_phone",
    "phone",
    "phone_number",
    "postal_code",
    "raw_payload",
    "ssn",
    "street",
    "street_address",
    "subscriber_id",
    "to_number",
    "user_number",
    "work_phone",
    "zip",
    "zip_code",
}


def trace_safe_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-compatible mapping with direct-PII fields redacted."""
    return {
        str(key): trace_safe_value(str(key), value)
        for key, value in mapping.items()
        if value is not None
    }


def trace_safe_value(key: str, value: Any) -> Any:
    normalized = key.lower().replace("-", "_")
    if normalized in PII_TRACE_KEYS:
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
