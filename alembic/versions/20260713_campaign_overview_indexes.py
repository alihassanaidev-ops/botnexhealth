"""Add campaign overview/run progress indexes.

Revision ID: 20260713_campaign_overview_indexes
Revises: 20260712_sms_wf_attribution
"""

from __future__ import annotations

from alembic import op

revision = "20260713_campaign_overview_indexes"
down_revision = "20260712_sms_wf_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic's default version table is varchar(32); this revision id is 34
    # chars, so widen before Alembic records the completed step.
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE varchar(64)")
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_runs_workflow_status_created "
        "ON automation_workflow_runs (workflow_id, status, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_runs_workflow_current_step "
        "ON automation_workflow_runs (workflow_id, current_step_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_runs_workflow_outcome "
        "ON automation_workflow_runs (workflow_id, outcome, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_automation_workflow_timers_run_status_due "
        "ON automation_workflow_timers (workflow_run_id, status, due_at)",
        "CREATE INDEX IF NOT EXISTS ix_automation_step_executions_run_type_status "
        "ON automation_workflow_step_executions (workflow_run_id, step_type, status)",
        "CREATE INDEX IF NOT EXISTS ix_sms_history_logs_workflow_run_timestamp "
        "ON sms_history_logs (workflow_run_id, timestamp) "
        "WHERE workflow_run_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_inbound_sms_messages_run_created "
        "ON inbound_sms_messages (workflow_run_id, created_at) "
        "WHERE workflow_run_id IS NOT NULL",
    ):
        op.execute(stmt)


def downgrade() -> None:
    for name in (
        "ix_inbound_sms_messages_run_created",
        "ix_sms_history_logs_workflow_run_timestamp",
        "ix_automation_step_executions_run_type_status",
        "ix_automation_workflow_timers_run_status_due",
        "ix_automation_workflow_runs_workflow_outcome",
        "ix_automation_workflow_runs_workflow_current_step",
        "ix_automation_workflow_runs_workflow_status_created",
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")
