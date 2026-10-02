"""Store live NexHealth webhook endpoint controls and signing secrets.

Revision ID: 20260903_nh_webhook_control
Revises: 20260902_merge_split_email
"""

from __future__ import annotations

from alembic import op


revision = "20260903_nh_webhook_control"
down_revision = "20260902_merge_split_email"
branch_labels = None
depends_on = None

TABLE = "nexhealth_webhook_subscriptions"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {TABLE}
        ADD COLUMN IF NOT EXISTS provider_subscription_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS callback_url varchar(500),
        ADD COLUMN IF NOT EXISTS secret_key_encrypted text
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {TABLE}
        DROP COLUMN IF EXISTS secret_key_encrypted,
        DROP COLUMN IF EXISTS callback_url,
        DROP COLUMN IF EXISTS provider_subscription_ids
        """
    )
