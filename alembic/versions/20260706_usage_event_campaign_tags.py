"""Add campaign attribution tags to usage_events (Plan 11 M-4).

Adds nullable workflow_run_id + workflow_id so per-campaign / per-run spend is
attributable, plus a partial index for campaign spend queries. institution_group
is intentionally NOT denormalized — group/DSO rollups JOIN institutions.group_id.

Idempotent (ADD COLUMN IF NOT EXISTS) to match repo convention: the consolidated
baseline builds schema from live model metadata on a fresh ``upgrade head``, so the
columns may already exist.

Revision ID: 20260706_usage_event_tags
Revises: 20260706_dnc_scope
"""

from __future__ import annotations

from alembic import op

revision = "20260706_usage_event_tags"
down_revision = "20260706_dnc_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE usage_events "
        "ADD COLUMN IF NOT EXISTS workflow_run_id UUID"
    )
    op.execute(
        "ALTER TABLE usage_events "
        "ADD COLUMN IF NOT EXISTS workflow_id UUID"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_usage_events_workflow_run_id "
        "ON usage_events (workflow_run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_usage_events_workflow "
        "ON usage_events (workflow_id, occurred_at) "
        "WHERE workflow_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_usage_events_workflow")
    op.execute("DROP INDEX IF EXISTS ix_usage_events_workflow_run_id")
    op.execute("ALTER TABLE usage_events DROP COLUMN IF EXISTS workflow_id")
    op.execute("ALTER TABLE usage_events DROP COLUMN IF EXISTS workflow_run_id")
