"""Persist Retell call-detail fields on calls.

Revision ID: 20260829_call_disconnection_reason
Revises: 20260829_merge_campaign_email_retell_sms
"""

from __future__ import annotations

from alembic import op


revision = "20260829_call_disconnection_reason"
down_revision = "20260829_merge_campaign_email_retell_sms"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
