"""Terminalize Retell SMS sessions when their workflow run is cancelled.

Revision ID: 20260828_retell_sms_cancel
Revises: 20260827_execution_snapshots
"""

from __future__ import annotations

from alembic import op


revision = "20260828_retell_sms_cancel"
down_revision = "20260827_execution_snapshots"
branch_labels = None
depends_on = None


_STATUS_CHECK_WITH_CANCELLED = """
    status IN (
        'awaiting_user', 'generating', 'completed', 'cancelled', 'handoff',
        'timed_out', 'failed', 'opted_out'
    )
"""

_STATUS_CHECK_LEGACY = """
    status IN (
        'awaiting_user', 'generating', 'completed', 'handoff',
        'timed_out', 'failed', 'opted_out'
    )
"""


def upgrade() -> None:
    op.execute(
        "ALTER TABLE retell_sms_sessions DROP CONSTRAINT ck_retell_sms_sessions_status"
    )
    op.execute(
        "ALTER TABLE retell_sms_sessions "
        "ADD CONSTRAINT ck_retell_sms_sessions_status "
        f"CHECK ({_STATUS_CHECK_WITH_CANCELLED})"
    )
    op.execute(
        """
        UPDATE retell_sms_sessions AS session
        SET status = 'cancelled',
            terminal_outcome = 'workflow_cancelled',
            ended_at = COALESCE(session.ended_at, run.cancelled_at, now())
        FROM automation_workflow_runs AS run
        WHERE session.workflow_run_id = run.id
          AND run.status = 'cancelled'
          AND session.status IN ('awaiting_user', 'generating')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE retell_sms_sessions
        SET status = 'failed',
            terminal_outcome = COALESCE(terminal_outcome, 'workflow_cancelled')
        WHERE status = 'cancelled'
        """
    )
    op.execute(
        "ALTER TABLE retell_sms_sessions DROP CONSTRAINT ck_retell_sms_sessions_status"
    )
    op.execute(
        "ALTER TABLE retell_sms_sessions "
        "ADD CONSTRAINT ck_retell_sms_sessions_status "
        f"CHECK ({_STATUS_CHECK_LEGACY})"
    )
