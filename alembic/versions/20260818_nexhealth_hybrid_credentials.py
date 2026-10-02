"""Track NexHealth webhook credential ownership.

Revision ID: 20260818_nh_hybrid_creds
Revises: 20260815_nh_shadow_secret
"""

from __future__ import annotations

from alembic import op

revision = "20260818_nh_hybrid_creds"
down_revision = "20260815_nh_shadow_secret"
branch_labels = None
depends_on = None

SUBSCRIPTIONS_TABLE = "nexhealth_webhook_subscriptions"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {SUBSCRIPTIONS_TABLE}
        ADD COLUMN IF NOT EXISTS credential_mode varchar(32),
        ADD COLUMN IF NOT EXISTS api_key_hash varchar(64)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_{SUBSCRIPTIONS_TABLE}_api_key_hash
        ON {SUBSCRIPTIONS_TABLE} (api_key_hash)
        """
    )


def downgrade() -> None:
    op.execute(
        f"DROP INDEX IF EXISTS ix_{SUBSCRIPTIONS_TABLE}_api_key_hash"
    )
    op.execute(
        f"""
        ALTER TABLE {SUBSCRIPTIONS_TABLE}
        DROP COLUMN IF EXISTS api_key_hash,
        DROP COLUMN IF EXISTS credential_mode
        """
    )
