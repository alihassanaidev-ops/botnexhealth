"""Block TRUNCATE on audit_logs at the database level.

The baseline migration installs ``audit_logs_no_update`` and
``audit_logs_no_delete`` triggers that raise an exception on UPDATE
or DELETE — closing two of the three ways to wipe an audit row.
TRUNCATE is the third: ``BEFORE DELETE`` triggers do NOT fire on
TRUNCATE, so a privileged role (anyone holding ``DATABASE_ADMIN_URL``)
can run ``TRUNCATE audit_logs`` and silently destroy the legal
record. The runtime ``nexhealth_app`` role is already locked to
``SELECT, INSERT`` only, so this gap only opens for admin sessions —
but a careless ``psql`` script or a compromised admin secret would
hit it.

PostgreSQL supports statement-level ``BEFORE TRUNCATE`` triggers.
This migration adds one that raises the same exception, mirroring
the existing UPDATE/DELETE pattern. It does NOT block partition
pruning because partition retention drops child partitions via
``DROP TABLE`` (DDL on the child), not ``TRUNCATE`` on the parent.

Revision ID: 20260516_audit_truncate
Revises: 20260515_mfa
"""

from __future__ import annotations

from alembic import op


revision = "20260516_audit_truncate"
down_revision = "20260515_mfa"
branch_labels = None
depends_on = None


_TRUNCATE_TRIGGER_SQL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs;",
    """
    CREATE TRIGGER audit_logs_no_truncate
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT
        EXECUTE FUNCTION prevent_audit_log_mutation();
    """,
)


def upgrade() -> None:
    for stmt in _TRUNCATE_TRIGGER_SQL:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs;")
