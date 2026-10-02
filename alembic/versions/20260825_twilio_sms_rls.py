"""Allow inbound SMS workflow correlation and staff notifications.

Revision ID: 20260825_twilio_sms_rls
Revises: 20260824_remove_reply_keys
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import op


revision = "20260825_twilio_sms_rls"
down_revision = "20260824_remove_reply_keys"
branch_labels = None
depends_on = None


TWILIO_SELECT_POLICIES: tuple[tuple[str, str], ...] = (
    ("automation_workflow_runs", "automation_workflow_runs_twilio_select"),
    ("automation_workflow_versions", "automation_workflow_versions_twilio_select"),
    (
        "automation_workflow_step_executions",
        "automation_workflow_step_executions_twilio_select",
    ),
)

NOTIFICATION_TYPES: tuple[str, ...] = (
    "new_call",
    "callback_item",
    "callback_resolved",
    "appointment_booked",
    "urgent",
    "inbound_sms_reply",
)

PREVIOUS_NOTIFICATION_TYPES: tuple[str, ...] = NOTIFICATION_TYPES[:-1]


def _create_twilio_select_policy(table: str, policy: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute(
        f"""
        CREATE POLICY {policy} ON {table} FOR SELECT
        USING (
            app_rls_context_type() = 'twilio'
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR {table}.location_id IS NULL
                OR {table}.location_id = app_rls_location_id()
            )
        )
        """
    )


def _replace_notification_type_constraint(values: Iterable[str]) -> None:
    allowed = ", ".join(f"'{value}'" for value in values)
    op.execute(
        "ALTER TABLE notifications DROP CONSTRAINT IF EXISTS ck_notifications_type"
    )
    op.execute(
        "ALTER TABLE notifications "
        "ADD CONSTRAINT ck_notifications_type "
        f"CHECK (type IN ({allowed})) NOT VALID"
    )
    op.execute("ALTER TABLE notifications VALIDATE CONSTRAINT ck_notifications_type")


def upgrade() -> None:
    for table, policy in TWILIO_SELECT_POLICIES:
        _create_twilio_select_policy(table, policy)
    _replace_notification_type_constraint(NOTIFICATION_TYPES)


def downgrade() -> None:
    for table, policy in TWILIO_SELECT_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    _replace_notification_type_constraint(PREVIOUS_NOTIFICATION_TYPES)
