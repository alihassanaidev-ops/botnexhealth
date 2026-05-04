"""make audit_logs immutable (prevent UPDATE and DELETE)

Revision ID: 0003
Revises: 0001
Create Date: 2026-02-17

HIPAA §164.312(b) requires audit logs to be tamper-proof. This migration
creates a database-level trigger that prevents any UPDATE or DELETE on the
audit_logs table, ensuring the append-only invariant is enforced at the
PostgreSQL level — not just application code.

Only a superuser can modify or drop this trigger.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the trigger function that blocks mutations
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs table is append-only. UPDATE and DELETE are prohibited (HIPAA §164.312(b)).';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Attach trigger for UPDATE
    op.execute("""
        CREATE TRIGGER audit_logs_no_update
            BEFORE UPDATE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_mutation();
    """)

    # Attach trigger for DELETE
    op.execute("""
        CREATE TRIGGER audit_logs_no_delete
            BEFORE DELETE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs;")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_delete ON audit_logs;")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutation();")
