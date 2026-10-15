"""Enable tenant RLS on internal_status_events.

Revision ID: 20261015_internal_status_events_rls
Revises: 20260914_loc_tz_checked
Create Date: 2026-10-15

`internal_status_events` was created with an `institution_id` column but
without row-level security, so the startup check logged CRITICAL and any
session context could read every tenant's status transitions. This enables
and forces RLS with the same tenant policy the other event tables use, and
grants the unprivileged application role access.
"""

from __future__ import annotations

from alembic import op

revision = "20261015_internal_status_events_rls"
down_revision = "20260914_loc_tz_checked"
branch_labels = None
depends_on = None

_TABLE = "internal_status_events"


def _rls_expr(table: str) -> str:
    """Tenant policy: super-admin, service contexts, or a scoped user.

    Written inside the same transaction by the internal-status session
    listener and read by the trigger task, so both the ``user`` context the
    request ran under and the ``celery`` context the trigger runs under must
    be able to see the row.
    """
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter')
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR {table}.location_id IS NULL
                OR {table}.location_id = app_rls_location_id()
            )
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR {table}.location_id IS NULL
                OR {table}.location_id = app_rls_location_id()
            )
        )
    """


def upgrade() -> None:
    expr = _rls_expr(_TABLE)
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_rls ON {_TABLE}")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_rls ON {_TABLE} FOR ALL
        USING ({expr})
        WITH CHECK ({expr})
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    internal_status_events
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_rls ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
