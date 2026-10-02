"""Add durable raw payload storage for NexHealth webhook events.

Revision ID: 20260720_nexhealth_webhook_durability
Revises: 20260719_patient_backfill_watermarks
"""

from __future__ import annotations

from alembic import op

revision = "20260720_nexhealth_webhook_durability"
down_revision = "20260719_patient_backfill_watermarks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "ADD COLUMN IF NOT EXISTS source_event_id varchar(160)"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "ADD COLUMN IF NOT EXISTS payload_hash varchar(128)"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "ADD COLUMN IF NOT EXISTS redacted_payload_encrypted text"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "ADD COLUMN IF NOT EXISTS raw_payload_encrypted text"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "ADD COLUMN IF NOT EXISTS raw_payload_retain_until timestamptz"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "ADD COLUMN IF NOT EXISTS raw_payload_purged_at timestamptz"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nexhealth_webhook_events_source_event "
        "ON nexhealth_webhook_events (source_event_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nexhealth_webhook_events_payload_hash "
        "ON nexhealth_webhook_events (payload_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nexhealth_webhook_events_raw_retain "
        "ON nexhealth_webhook_events (raw_payload_retain_until)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nexhealth_webhook_events_raw_purged "
        "ON nexhealth_webhook_events (raw_payload_purged_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_nexhealth_webhook_events_raw_purged")
    op.execute("DROP INDEX IF EXISTS ix_nexhealth_webhook_events_raw_retain")
    op.execute("DROP INDEX IF EXISTS ix_nexhealth_webhook_events_payload_hash")
    op.execute("DROP INDEX IF EXISTS ix_nexhealth_webhook_events_source_event")
    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "DROP COLUMN IF EXISTS raw_payload_purged_at"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "DROP COLUMN IF EXISTS raw_payload_retain_until"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "DROP COLUMN IF EXISTS raw_payload_encrypted"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_events "
        "DROP COLUMN IF EXISTS redacted_payload_encrypted"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_events DROP COLUMN IF EXISTS payload_hash"
    )
    op.execute(
        "ALTER TABLE nexhealth_webhook_events DROP COLUMN IF EXISTS source_event_id"
    )
