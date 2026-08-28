"""Remove campaign SMS conversation reply keys.

Revision ID: 20260824_remove_reply_keys
Revises: 20260821_campaign_threads
"""

from __future__ import annotations

from alembic import op

revision = "20260824_remove_reply_keys"
down_revision = "20260821_campaign_threads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_campaign_conversation_threads_reply_key")
    op.execute(
        "ALTER TABLE campaign_conversation_threads DROP COLUMN IF EXISTS reply_key"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE campaign_conversation_threads ADD COLUMN IF NOT EXISTS reply_key varchar(12)"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_campaign_conversation_threads_reply_key
        ON campaign_conversation_threads
        (institution_id, location_id, channel, reply_key, status)
        """
    )
