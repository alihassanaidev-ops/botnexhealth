"""Add outbound_emergency_halts table for Plan 12 compliance gate.

Append-only audit table: one active row (released_at IS NULL) per institution
blocks all outbound campaign sends via the ComplianceGateService.

Revision ID: 20260703_outbound_halt
Revises: 20260702_auto_workflow_core
"""

from __future__ import annotations

from alembic import op

revision = "20260703_outbound_halt"
down_revision = "20260702_auto_workflow_core"
branch_labels = None
depends_on = None

_TABLE = "outbound_emergency_halts"

_RLS_EXPR = f"""
    app_rls_is_super_admin()
    OR (
        app_rls_context_type() IN ('celery', 'dead_letter')
        AND {_TABLE}.institution_id = app_rls_institution_id()
    )
    OR (
        app_rls_context_type() = 'user'
        AND {_TABLE}.institution_id = app_rls_institution_id()
        AND app_rls_role() = 'INSTITUTION_ADMIN'
    )
"""


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id        uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            halted_by_user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
            reason                text,
            created_at            timestamptz NOT NULL DEFAULT now(),
            released_at           timestamptz,
            released_by_user_id   uuid REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    # Partial index for active-halt lookup (not expressible via SQLAlchemy index=True)
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{_TABLE}_institution_active "
        f"ON {_TABLE} (institution_id) WHERE released_at IS NULL"
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_rls ON {_TABLE}")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_rls ON {_TABLE} FOR ALL
        USING ({_RLS_EXPR})
        WITH CHECK ({_RLS_EXPR})
        """
    )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE ON {_TABLE} TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_rls ON {_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
