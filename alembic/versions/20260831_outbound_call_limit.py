"""Per-clinic ceiling on simultaneous outbound calls.

Revision ID: 20260831_outbound_call_limit
Revises: 20260831_call_notes

Item 18 needs a configurable per-clinic ceiling. This is the override column;
NULL means "use ``settings.outbound_call_concurrency_limit``" rather than "no
limit", so a clinic nobody has tuned is still bounded.

Nullable with no server default and no backfill: every existing clinic keeps
the platform default, and the column carries a value only where an operator has
deliberately set one. That way the platform default stays a single knob rather
than something already frozen into every row.

DDL is guarded with IF NOT EXISTS because the consolidated baseline builds the
whole schema from the models via ``create_all`` — on a fresh database this
column already exists by the time this migration runs.
"""

from __future__ import annotations

from alembic import op

revision = "20260831_outbound_call_limit"
down_revision = "20260831_call_notes"
branch_labels = None
depends_on = None

TABLE = "institutions"
COLUMN = "outbound_call_limit"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {COLUMN} INTEGER NULL"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {COLUMN}")
