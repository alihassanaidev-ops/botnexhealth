"""Add local visibility flag for cached operatories.

Revision ID: 20260821_hide_operatories
Revises: 20260818_nh_hybrid_creds
"""

from __future__ import annotations

from alembic import op

revision = "20260821_hide_operatories"
down_revision = "20260818_nh_hybrid_creds"
branch_labels = None
depends_on = None

TABLE = "institution_operatories"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {TABLE}
        ADD COLUMN IF NOT EXISTS is_hidden boolean NOT NULL DEFAULT false
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {TABLE}
        DROP COLUMN IF EXISTS is_hidden
        """
    )
