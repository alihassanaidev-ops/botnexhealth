"""Institution-level switch for automatic staff notification emails.

Revision ID: 20260905_staff_notif
Revises: 20260904_workflow_schedules
"""

from __future__ import annotations

from alembic import op


revision = "20260905_staff_notif"
down_revision = "20260904_workflow_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Default true so every existing institution keeps the behaviour it has.
    op.execute(
        "ALTER TABLE institutions "
        "ADD COLUMN IF NOT EXISTS staff_notification_emails_enabled boolean "
        "NOT NULL DEFAULT true"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE institutions DROP COLUMN staff_notification_emails_enabled"
    )
