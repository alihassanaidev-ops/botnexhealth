"""Cache GoTracker appointment status snapshot.

Revision ID: 20260804_gotracker_status_snapshot
Revises: 20260801_gotracker_location_webhook_secret
"""

from __future__ import annotations

from alembic import op

revision = "20260804_gotracker_status_snapshot"
down_revision = "20260801_gotracker_location_webhook_secret"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE appointment_working_set
            ADD COLUMN IF NOT EXISTS gotracker_status_id integer,
            ADD COLUMN IF NOT EXISTS gotracker_status_label varchar(40),
            ADD COLUMN IF NOT EXISTS is_confirmed boolean,
            ADD COLUMN IF NOT EXISTS is_preconfirmed boolean,
            ADD COLUMN IF NOT EXISTS last_status_source varchar(40),
            ADD COLUMN IF NOT EXISTS last_status_synced_at timestamptz,
            ADD COLUMN IF NOT EXISTS last_writeback_at timestamptz
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appointment_working_set_gotracker_status "
        "ON appointment_working_set (institution_id, gotracker_status_id)"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_appointment_working_set_gotracker_status"
    )
    op.execute(
        """
        ALTER TABLE appointment_working_set
            DROP COLUMN IF EXISTS last_writeback_at,
            DROP COLUMN IF EXISTS last_status_synced_at,
            DROP COLUMN IF EXISTS last_status_source,
            DROP COLUMN IF EXISTS is_preconfirmed,
            DROP COLUMN IF EXISTS is_confirmed,
            DROP COLUMN IF EXISTS gotracker_status_label,
            DROP COLUMN IF EXISTS gotracker_status_id
        """
    )
