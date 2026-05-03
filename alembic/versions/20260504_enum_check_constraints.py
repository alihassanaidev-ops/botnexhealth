"""Add enum check constraints.

Revision ID: 20260504_enum_check_constraints
Revises: 20260503_retell_function_idempotency
Create Date: 2026-05-04
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260504_enum_check_constraints"
down_revision: Union[str, None] = "20260503_retell_function_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINTS = (
    (
        "ck_users_role",
        "users",
        "role",
        ("SUPER_ADMIN", "INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"),
    ),
    (
        "ck_calls_call_status",
        "calls",
        "call_status",
        (
            "appointment_booked",
            "appointment_rescheduled",
            "appointment_cancelled",
            "emergency",
            "complaint",
            "needs_callback",
            "faq_handled",
            "financial_inquiry",
            "transferred",
            "insurance_verified",
            "insurance_unverified",
            "no_action_needed",
        ),
    ),
    (
        "ck_calls_call_direction",
        "calls",
        "call_direction",
        ("inbound", "outbound"),
    ),
    (
        "ck_custom_field_definitions_entity_type",
        "custom_field_definitions",
        "entity_type",
        ("contact", "call"),
    ),
    (
        "ck_custom_field_definitions_field_type",
        "custom_field_definitions",
        "field_type",
        ("text", "number", "boolean", "date", "dropdown"),
    ),
    (
        "ck_notifications_type",
        "notifications",
        "type",
        (
            "new_call",
            "callback_item",
            "callback_resolved",
            "appointment_booked",
            "urgent",
        ),
    ),
)


def _in_constraint(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    for constraint_name, table_name, column_name, allowed_values in _CONSTRAINTS:
        op.create_check_constraint(
            constraint_name,
            table_name,
            _in_constraint(column_name, allowed_values),
        )


def downgrade() -> None:
    for constraint_name, table_name, _column_name, _allowed_values in reversed(
        _CONSTRAINTS
    ):
        op.drop_constraint(constraint_name, table_name, type_="check")
