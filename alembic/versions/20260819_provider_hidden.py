"""Add institution_providers.is_hidden — operator-owned provider visibility.

Lets an operator hide individual providers, per location, from the Retell
`list_providers` tool. NexHealth returns a clinic's full historical roster;
only a few rows are real bookable staff.

Deliberately a NEW column rather than reusing `is_active`: the PMS sync rewrites
`is_active = True` for every provider it sees on every run (see
SyncService._upsert_provider), so it records "seen in the last sync", not
operator intent. Nothing in the sync path touches `is_hidden`.

Additive with a server default, so existing rows default to visible and the
column is safe to add before or after the app rollout. Idempotent
(IF NOT EXISTS) for pre-apply on live prod.
"""

from alembic import op

revision = "20260819_provider_hidden"
down_revision = "20260720_call_scrubbed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE institution_providers ADD COLUMN IF NOT EXISTS "
        "is_hidden BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE institution_providers DROP COLUMN IF EXISTS is_hidden"
    )
