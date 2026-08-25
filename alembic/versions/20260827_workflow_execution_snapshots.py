"""Persist PHI-safe workflow step input and output snapshots.

Revision ID: 20260827_execution_snapshots
Revises: 20260826_retell_sms
"""

from __future__ import annotations

from alembic import op


revision = "20260827_execution_snapshots"
down_revision = "20260826_retell_sms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE automation_workflow_step_executions "
        "ADD COLUMN input_snapshot jsonb, "
        "ADD COLUMN output_snapshot jsonb"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE automation_workflow_step_executions "
        "DROP COLUMN output_snapshot, "
        "DROP COLUMN input_snapshot"
    )
