"""Add audit log actor/location columns and soft-delete timestamp for users.

Revision ID: 20260423_audit_user_soft_delete
Revises: 20260420_dashboard_call_indexes
Create Date: 2026-04-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "20260423_audit_user_soft_delete"
down_revision: Union[str, None] = "20260420_dashboard_call_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UUID_REGEX = (
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    "[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def _restore_audit_log_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs table is append-only. UPDATE and DELETE are prohibited (HIPAA §164.312(b)).';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_update
            BEFORE UPDATE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_delete
            BEFORE DELETE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_mutation();
        """
    )


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("user_id", UUID(as_uuid=False), nullable=True))
    op.add_column("audit_logs", sa.Column("location_id", UUID(as_uuid=False), nullable=True))
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_location_id", "audit_logs", ["location_id"])

    op.add_column(
        "users",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])

    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs;")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_delete ON audit_logs;")

    op.execute(
        f"""
        UPDATE audit_logs
        SET
            user_id = CASE
                WHEN COALESCE(audit_metadata->>'actor_user_id', '') ~* '{UUID_REGEX}'
                    THEN (audit_metadata->>'actor_user_id')::uuid
                WHEN COALESCE(audit_metadata->>'user_id', '') ~* '{UUID_REGEX}'
                    THEN (audit_metadata->>'user_id')::uuid
                WHEN COALESCE(actor, '') ~* '{UUID_REGEX}'
                    THEN actor::uuid
                ELSE NULL
            END,
            location_id = CASE
                WHEN COALESCE(audit_metadata->>'location_id', '') ~* '{UUID_REGEX}'
                    THEN (audit_metadata->>'location_id')::uuid
                ELSE NULL
            END
        """
    )

    _restore_audit_log_triggers()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs;")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_delete ON audit_logs;")

    op.drop_index("ix_users_deleted_at", table_name="users")
    op.drop_column("users", "deleted_at")

    op.drop_index("ix_audit_logs_location_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
    op.drop_column("audit_logs", "location_id")
    op.drop_column("audit_logs", "user_id")

    _restore_audit_log_triggers()
