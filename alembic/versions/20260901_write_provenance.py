"""Record what caused each write into a practice's records.

Revision ID: 20260901_write_provenance
Revises: 20260831_quiet_hours_exceptions

Item 34. The queued write already carried a campaign run id; it carried nothing
about *who or what* decided, and nothing tying it to the interaction that
produced it.

``actor`` is the missing distinction. A run id alone cannot tell a campaign
step apart from a patient acting on a link the campaign sent — both carry one,
and an investigation into an unexpected booking needs to know which.

``trace_id`` is indexed because that is the question an investigation actually
asks: given this identifier from a log line or a support ticket, what did we
write? Without the index that is a sequential scan of every write ever queued.

All three are nullable with no backfill. Rows written before this exist and
cannot be given a cause after the fact; inventing one would be worse than
leaving it blank, because a fabricated actor reads exactly like a real one.

DDL is guarded with IF NOT EXISTS because the consolidated baseline builds the
whole schema from the models via ``create_all`` — on a fresh database these
columns already exist by the time this migration runs.
"""

from __future__ import annotations

from alembic import op

revision = "20260901_write_provenance"
down_revision = "20260831_quiet_hours_exceptions"
branch_labels = None
depends_on = None

TABLE = "gotracker_appointment_writebacks"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS actor varchar(32)")
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS trace_id varchar(64)")
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS reason text")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_gotracker_appointment_writebacks_trace_id "
        f"ON {TABLE} (trace_id)"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_gotracker_appointment_writebacks_trace_id"
    )
    for column in ("reason", "trace_id", "actor"):
        op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {column}")
