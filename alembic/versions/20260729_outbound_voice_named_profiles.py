"""Allow multiple named outbound voice profiles per location.

Revision ID: 20260729_outbound_voice_named_profiles
Revises: 20260728_fix_inst_rls_recursion
"""

from __future__ import annotations

from alembic import op

revision = "20260729_outbound_voice_named_profiles"
down_revision = "20260728_fix_inst_rls_recursion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE outbound_voice_profiles
        ADD COLUMN IF NOT EXISTS purpose varchar(80)
        """
    )
    op.execute("DROP INDEX IF EXISTS uq_outbound_voice_profiles_active_location")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_outbound_voice_profiles_active_location_purpose
        ON outbound_voice_profiles (location_id, purpose)
        WHERE is_active = true AND purpose IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_outbound_voice_profiles_location_active
        ON outbound_voice_profiles (location_id, is_active)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_outbound_voice_profiles_active_location_purpose")
    op.execute("DROP INDEX IF EXISTS ix_outbound_voice_profiles_location_active")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_outbound_voice_profiles_active_location
        ON outbound_voice_profiles (location_id)
        WHERE is_active = true
        """
    )
    op.execute("ALTER TABLE outbound_voice_profiles DROP COLUMN IF EXISTS purpose")
