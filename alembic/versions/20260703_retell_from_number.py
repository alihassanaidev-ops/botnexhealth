"""Add per-location Retell outbound caller-ID (Plan 03).

Adds a nullable retell_from_number column to institution_locations. This is the
E.164 number (imported into Retell) used as the caller ID for outbound campaign
voice calls. Distinct from twilio_from_number (SMS).

Revision ID: 20260703_retell_from_number
Revises: 20260704_usage_events

Merge note: re-chained after 20260704_usage_events (was 20260703_provisioning) so
the Plan-03 branch and the Plan-11 usage_events migration form a single linear
head instead of two divergent heads. Made idempotent (ADD COLUMN IF NOT EXISTS)
to match the repo convention — the consolidated baseline builds the schema from
live model metadata (Base.metadata.create_all), so on a fresh ``upgrade head`` the
column already exists; bare op.add_column would raise DuplicateColumnError.
"""

from __future__ import annotations

from alembic import op

revision = "20260703_retell_from_number"
down_revision = "20260704_usage_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE institution_locations "
        "ADD COLUMN IF NOT EXISTS retell_from_number VARCHAR(20)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE institution_locations DROP COLUMN IF EXISTS retell_from_number")
