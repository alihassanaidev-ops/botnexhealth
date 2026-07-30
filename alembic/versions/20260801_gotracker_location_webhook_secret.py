"""Store GoTracker webhook secret per location.

Revision ID: 20260801_gotracker_location_webhook_secret
Revises: 20260731_workflow_drip_action
"""

from __future__ import annotations

from alembic import op

revision = "20260801_gotracker_location_webhook_secret"
down_revision = "20260731_workflow_drip_action"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE institution_locations "
        "ADD COLUMN IF NOT EXISTS gotracker_webhook_secret_encrypted TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE institution_locations "
        "DROP COLUMN IF EXISTS gotracker_webhook_secret_encrypted"
    )
