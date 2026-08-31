"""Add staff-authored notes on call records.

Revision ID: 20260831_call_notes
Revises: 20260830_sms_staff_templates

The note body is application-encrypted PHI, so the table gets the same
FORCE RLS treatment as ``calls``: a note is reachable exactly when its parent
call is. ``institution_id``/``location_id`` are denormalized from the call at
write time so the policy needs no join.

DDL is guarded with IF NOT EXISTS because the consolidated baseline builds the
whole schema from the models via ``create_all`` — on a fresh database this
table already exists by the time this migration runs.
"""

from __future__ import annotations

from alembic import op

revision = "20260831_call_notes"
# Chained onto production's head. On staging this sits after the enquiry
# store; production never took that chain, and call_notes depends on none
# of it — only calls, institutions, institution_locations and users.
down_revision = "20260830_sms_staff_templates"
branch_labels = None
depends_on = None

TABLE = "call_notes"
#: Spelled out rather than interpolated so the policy name is greppable —
#: tests/unit/test_rls_protected_tables_coverage.py scans migration source text
#: for "<table>_rls" to prove a post-baseline table is protected.
POLICY = "call_notes_rls"


def _rls_expr(table: str) -> str:
    """Mirror of the ``calls`` policy — a note follows its call's visibility.

    Background contexts ('celery', 'dead_letter') are allowed for retention
    sweeps; 'retell' is deliberately absent because the voice agent never
    reads or writes staff notes.
    """
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter')
            AND {table}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR {table}.location_id = app_rls_location_id()
            )
        )
    """


def _grant(table: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def _enable_rls(table: str) -> None:
    expr = _rls_expr(table)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
    op.execute(
        f"CREATE POLICY {POLICY} ON {table} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )
    _grant(table)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS call_notes (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            call_id             uuid NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
            institution_id      uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id         uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            author_user_id      uuid REFERENCES users(id) ON DELETE SET NULL,
            author_email        varchar(255) NOT NULL,
            body_encrypted      text NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            edited_at           timestamptz,
            deleted_at          timestamptz,
            deleted_by_user_id  uuid REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_call_notes_call_id ON call_notes (call_id)"
    )
    # The thread read: every note on one call, oldest first.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_call_notes_call_created "
        "ON call_notes (call_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_call_notes_institution "
        "ON call_notes (institution_id)"
    )

    _enable_rls(TABLE)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
