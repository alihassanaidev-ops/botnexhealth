"""Persist Retell call-detail fields on calls.

Revision ID: 20260829_call_disconnection_reason
Revises: 20260819_provider_hidden

On the production branch this revises 20260819_provider_hidden (staging's
intermediate campaign/email/retell-sms chain is not deployed there).
"""

from __future__ import annotations

from alembic import op


revision = "20260829_call_disconnection_reason"
down_revision = "20260819_provider_hidden"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic's default version table is varchar(32); this revision id is 34
    # chars, so widen before Alembic records the completed step. (Staging did
    # this in 20260713_campaign_overview_indexes, which production lacks.)
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE varchar(64)")
    op.execute(
        """
        ALTER TABLE calls
        ADD COLUMN IF NOT EXISTS disconnection_reason varchar(80),
        ADD COLUMN IF NOT EXISTS requested_availability text
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE calls
        DROP COLUMN IF EXISTS requested_availability,
        DROP COLUMN IF EXISTS disconnection_reason
        """
    )
