"""Per-location intake endpoints so an external form can land a lead.

Decision C settled the shape: one authenticated, rate-limited, idempotent
endpoint that the clinic's own site or a form provider they control posts to —
no per-source adapters and no OAuth into third-party CRMs. This is the table
behind that: a clinic can issue a credential per form, name it, and revoke it
without disturbing the others.

The token is stored as a keyed hash, never in the clear, for the same reason a
password is: the row is the thing an attacker reaches first, and a token that
can be read out of a backup is a live intake endpoint for that clinic.

RLS is granted by *adding* a narrow policy rather than rewriting the existing
ones. The policies on these tables are long, and editing them by hand to append
one array element risks silently dropping a clause that is protecting something
else. Postgres ORs permissive policies together, so a small separate policy says
exactly what the new context may do and leaves everything already there intact.

Revision ID: 20260901_enquiry_intake_src
Revises: 20260901_enquiry_lead_fields
"""

from __future__ import annotations

from alembic import op

revision = "20260901_enquiry_intake_src"
down_revision = "20260901_enquiry_lead_fields"
branch_labels = None
depends_on = None

TABLE = "enquiry_intake_sources"

#: The context a public intake request runs under. Narrow on purpose: it can
#: reach exactly the three tables intake needs, in one institution.
CONTEXT = "enquiry_intake"

#: Tables the intake path touches: the enquiry it writes, the contact it checks
#: for an existing patient, and the consent it records.
INTAKE_TABLES = ("campaign_enquiries", "contacts", "consent_records")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id  uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id     uuid REFERENCES institution_locations(id) ON DELETE CASCADE,
            label           varchar(120) NOT NULL,
            token_hash      varchar(64)  NOT NULL,
            signing_secret_encrypted text,
            source_name     varchar(80)  NOT NULL DEFAULT 'external_form',
            default_attribution jsonb,
            is_active       boolean NOT NULL DEFAULT true,
            created_at      timestamptz NOT NULL DEFAULT now(),
            last_used_at    timestamptz
        )
        """
    )
    # The lookup key for every intake request, so it must be unique platform
    # wide rather than per institution — the token is what *identifies* the
    # institution, so it cannot be scoped by one.
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{TABLE}_token_hash "
        f"ON {TABLE} (token_hash)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_institution "
        f"ON {TABLE} (institution_id, is_active)"
    )

    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute(
        f"""
        CREATE POLICY {TABLE}_rls ON {TABLE} FOR ALL
        USING (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() = 'user'
                AND institution_id = app_rls_institution_id()
                AND app_rls_role() IN ('INSTITUTION_ADMIN', 'GROUP_ADMIN')
            )
        )
        WITH CHECK (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() = 'user'
                AND institution_id = app_rls_institution_id()
                AND app_rls_role() IN ('INSTITUTION_ADMIN', 'GROUP_ADMIN')
            )
        )
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO nexhealth_app;
            END IF;
        END
        $$
        """
    )

    # The intake request itself resolves its own source row before it has any
    # tenant context, so it needs to read this table under the intake context.
    for table in (TABLE, *INTAKE_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_enquiry_intake ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_enquiry_intake ON {table} FOR ALL
            USING (
                app_rls_context_type() = '{CONTEXT}'
                AND institution_id = app_rls_institution_id()
            )
            WITH CHECK (
                app_rls_context_type() = '{CONTEXT}'
                AND institution_id = app_rls_institution_id()
            )
            """
        )


def downgrade() -> None:
    for table in (TABLE, *INTAKE_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_enquiry_intake ON {table}")
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
