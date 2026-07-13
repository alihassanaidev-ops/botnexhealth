"""Add campaign-attribution columns to sms_history_logs (Plan 11 fix).

Stamps the workflow run/version that sent each SMS so the Twilio delivery-status
webhook can attribute usage/spend to the campaign in /by-campaign (previously SMS
was invisible in per-campaign spend). Plain nullable tags, no FK — attribution only.

Revision ID: 20260712_sms_wf_attribution
Revises: 20260710_usage_group_rls
"""

from __future__ import annotations

from alembic import op

revision = "20260712_sms_wf_attribution"
down_revision = "20260710_usage_group_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE sms_history_logs "
        "ADD COLUMN IF NOT EXISTS workflow_run_id UUID"
    )
    op.execute(
        "ALTER TABLE sms_history_logs "
        "ADD COLUMN IF NOT EXISTS workflow_id UUID"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sms_history_logs DROP COLUMN IF EXISTS workflow_id")
    op.execute("ALTER TABLE sms_history_logs DROP COLUMN IF EXISTS workflow_run_id")
