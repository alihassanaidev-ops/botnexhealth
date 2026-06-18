"""Workflow Status: tenant-defined, human-assigned call workflow states.

Adds:
- workflow_statuses table (institution-scoped RLS, same shape as other
  institution-owned tables: any user in the institution may read/write under
  RLS; role enforcement for definition CRUD lives at the route layer).
- calls.workflow_status_id (nullable FK, SET NULL on status delete) + a
  composite index for fast indexed-equality status filtering.
- Seeds the default status set for every existing institution.

Revision ID: 20260621_workflow_status
Revises: 20260620_group_agg_rls
"""

from __future__ import annotations

from alembic import op


revision = "20260621_workflow_status"
down_revision = "20260620_group_agg_rls"
branch_labels = None
depends_on = None


# Institution-owned policy (verbatim shape of the custom_field_definitions
# baseline policy): super admin, system contexts, and the owning institution's
# users may read/write their own rows.
_OWNED = """
    app_rls_is_super_admin()
    OR (
        app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
        AND workflow_statuses.institution_id = app_rls_institution_id()
    )
    OR (
        app_rls_context_type() = 'user'
        AND workflow_statuses.institution_id = app_rls_institution_id()
    )
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_statuses (
            id              uuid PRIMARY KEY,
            institution_id  uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            name            varchar(60) NOT NULL,
            color           varchar(20) NOT NULL DEFAULT 'zinc',
            display_order   integer NOT NULL DEFAULT 0,
            is_active       boolean NOT NULL DEFAULT true,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_workflow_status_institution_name UNIQUE (institution_id, name)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_statuses_institution_id "
        "ON workflow_statuses (institution_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_status_institution_order "
        "ON workflow_statuses (institution_id, display_order)"
    )

    # --- calls.workflow_status_id FK + filter index ---
    op.execute(
        "ALTER TABLE calls ADD COLUMN IF NOT EXISTS workflow_status_id uuid "
        "REFERENCES workflow_statuses(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_call_institution_workflow_status "
        "ON calls (institution_id, workflow_status_id, created_at)"
    )

    # --- RLS ---
    op.execute("ALTER TABLE workflow_statuses ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_statuses FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS workflow_statuses_rls ON workflow_statuses")
    op.execute(
        f"""
        CREATE POLICY workflow_statuses_rls ON workflow_statuses FOR ALL
        USING ({_OWNED})
        WITH CHECK ({_OWNED})
        """
    )

    # --- grant the runtime app role access (no-op on fresh DB) ---
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_statuses TO nexhealth_app;
            END IF;
        END
        $$
        """
    )

    # --- seed defaults for every existing institution ---
    # Runs under the migration's SUPER_ADMIN bootstrap context (bypasses RLS).
    op.execute(
        """
        INSERT INTO workflow_statuses (id, institution_id, name, color, display_order)
        SELECT gen_random_uuid(), i.id, d.name, d.color, d.ord
        FROM institutions i
        CROSS JOIN (VALUES
            ('Pending', 'amber', 0),
            ('In Progress', 'blue', 1),
            ('Completed', 'emerald', 2),
            ('Not Completed', 'rose', 3),
            ('Reviewed', 'violet', 4)
        ) AS d(name, color, ord)
        ON CONFLICT (institution_id, name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE calls DROP COLUMN IF EXISTS workflow_status_id")
    op.execute("DROP POLICY IF EXISTS workflow_statuses_rls ON workflow_statuses")
    op.execute("DROP TABLE IF EXISTS workflow_statuses")
