"""Patient backfill and reconciliation watermarks.

Revision ID: 20260719_patient_backfill_watermarks
Revises: 20260718_nexhealth_sync_status
"""

from __future__ import annotations

from alembic import op

revision = "20260719_patient_backfill_watermarks"
down_revision = "20260718_nexhealth_sync_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE nexhealth_webhook_subscriptions "
        "ADD COLUMN IF NOT EXISTS last_patient_backfill_at timestamptz"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_subscriptions "
        "ADD COLUMN IF NOT EXISTS last_patient_reconciliation_at timestamptz"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE nexhealth_webhook_subscriptions "
        "DROP COLUMN IF EXISTS last_patient_reconciliation_at"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_subscriptions "
        "DROP COLUMN IF EXISTS last_patient_backfill_at"
    )
