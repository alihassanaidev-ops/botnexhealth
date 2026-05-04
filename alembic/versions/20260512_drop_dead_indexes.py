"""Drop dead indexes and add idempotency-cleanup helper indexes.

After commit 2d29e63 the dashboard scopes by ``Call.location_id``
instead of ``Call.agent_used`` (which was Retell metadata, not the
authoritative tenant scope). The ``ix_call_institution_agent_date``
index is now dead weight on every INSERT to ``calls`` and the
planner won't pick it for any query the codebase issues. Drop it.

Also add ``created_at`` btree indexes on the three idempotency-style
tables so the periodic cleanup job (deletes rows older than N days)
can use an index range scan instead of a full table scan.

Revision ID: 20260512_drop_dead
Revises: 20260510_baseline
"""

from __future__ import annotations

from alembic import op


revision = "20260512_drop_dead"
down_revision = "20260510_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Stale dashboard index — dashboard now scopes by location_id.
    op.execute("DROP INDEX IF EXISTS ix_call_institution_agent_date")

    # Idempotency tables: created_at indexes for the periodic cleanup job.
    # ``retell_function_invocations`` and ``retell_webhook_events`` already
    # cluster well by their natural scopes (call_id, institution_id), but
    # the cleanup job filters strictly on ``created_at < <cutoff>`` and the
    # planner needs an index there to avoid a sequential scan once these
    # tables grow past a few hundred thousand rows.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_retell_function_invocations_created_at "
        "ON retell_function_invocations (created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_retell_webhook_events_created_at "
        "ON retell_webhook_events (created_at)"
    )
    # ``dead_letter_events`` already has ix_dead_letter_events_created_at
    # from the baseline; nothing to add.


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_retell_webhook_events_created_at")
    op.execute("DROP INDEX IF EXISTS ix_retell_function_invocations_created_at")
    # Restore the dropped index. The original definition.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_call_institution_agent_date "
        "ON calls (institution_id, agent_used, call_date)"
    )
