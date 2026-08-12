"""Persist GoTracker patient-flow data for post-op workflows.

Revision ID: 20260812_postop_flow_state
Revises: 20260812_retell_profile_lookup
"""

from __future__ import annotations

from alembic import op

revision = "20260812_postop_flow_state"
down_revision = "20260812_retell_profile_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE appointment_working_set
            ADD COLUMN IF NOT EXISTS appointment_reason varchar(500),
            ADD COLUMN IF NOT EXISTS flow_state varchar(120),
            ADD COLUMN IF NOT EXISTS flow_changed_at timestamptz,
            ADD COLUMN IF NOT EXISTS checked_in_at timestamptz,
            ADD COLUMN IF NOT EXISTS in_chair_at timestamptz,
            ADD COLUMN IF NOT EXISTS out_chair_at timestamptz,
            ADD COLUMN IF NOT EXISTS checked_out_at timestamptz
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appointment_working_set_flow_state "
        "ON appointment_working_set (institution_id, flow_state, flow_changed_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_appointment_working_set_flow_state")
    op.execute(
        """
        ALTER TABLE appointment_working_set
            DROP COLUMN IF EXISTS checked_out_at,
            DROP COLUMN IF EXISTS out_chair_at,
            DROP COLUMN IF EXISTS in_chair_at,
            DROP COLUMN IF EXISTS checked_in_at,
            DROP COLUMN IF EXISTS flow_changed_at,
            DROP COLUMN IF EXISTS flow_state,
            DROP COLUMN IF EXISTS appointment_reason
        """
    )
