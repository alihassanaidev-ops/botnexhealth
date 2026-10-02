"""Persist NexHealth shadow webhook endpoint signing secrets.

Revision ID: 20260815_nh_shadow_secret
Revises: 20260814_nh_shadow_webhooks
"""

from __future__ import annotations

from alembic import op

revision = "20260815_nh_shadow_secret"
down_revision = "20260814_nh_shadow_webhooks"
branch_labels = None
depends_on = None

SUBSCRIPTIONS_TABLE = "nexhealth_webhook_shadow_subscriptions"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {SUBSCRIPTIONS_TABLE}
        ADD COLUMN IF NOT EXISTS secret_key_encrypted text
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {SUBSCRIPTIONS_TABLE}
        DROP COLUMN IF EXISTS secret_key_encrypted
        """
    )
