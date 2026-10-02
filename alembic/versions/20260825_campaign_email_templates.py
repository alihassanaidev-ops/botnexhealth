"""Clinic-authored campaign email templates.

Adds ``campaign_email_templates`` — free-form, clinic-named templates a
``send_email`` workflow node can reference by ``key`` and reuse across
campaigns.

Deliberately a new table rather than a ``scope`` column on ``email_templates``:
that table holds the five fixed system notification types keyed by a closed
enum, rendering a call-centric variable set, and widening its
``(institution_id, template_type)`` unique index would mean touching the working
call-notification path for no gain.

Same institution-owned FORCE RLS shape as email_templates / sms_templates.

Revision ID: 20260825_campaign_email_tpl
Revises: 20260824_remove_reply_keys
"""

from __future__ import annotations

from alembic import op


revision = "20260825_campaign_email_tpl"
down_revision = "20260824_remove_reply_keys"
branch_labels = None
depends_on = None


# Institution-owned RLS policy — same shape as sms_templates / email_templates:
# super admin, the system contexts that render and send (celery workers), and
# the owning institution's users may read/write their own rows.
def _owned(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
            AND {table}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
        )
    """


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_email_templates (
            id                uuid PRIMARY KEY,
            institution_id    uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            key               varchar(80) NOT NULL,
            name              varchar(255) NOT NULL,
            subject_template  varchar(500) NOT NULL,
            html_body         text NOT NULL,
            text_body         text NOT NULL,
            is_active         boolean NOT NULL DEFAULT true,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_campaign_email_templates_key_format
                CHECK (key ~ '^[a-z0-9][a-z0-9_-]{0,79}$')
        )
        """
    )
    # Keys are referenced from published workflow definitions, so they must be
    # unique per institution and stable.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_campaign_email_template_institution_key "
        "ON campaign_email_templates (institution_id, key)"
    )

    # ── RLS (FORCE) + owned policy + runtime-role grants ─────────────────
    # Literal table name so the policy is greppable by the RLS-coverage
    # governance test (tests/unit/test_rls_protected_tables_coverage.py).
    op.execute("ALTER TABLE campaign_email_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE campaign_email_templates FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS campaign_email_templates_rls ON campaign_email_templates"
    )
    op.execute(
        f"""
        CREATE POLICY campaign_email_templates_rls ON campaign_email_templates FOR ALL
        USING ({_owned("campaign_email_templates")})
        WITH CHECK ({_owned("campaign_email_templates")})
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON campaign_email_templates
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS campaign_email_templates_rls ON campaign_email_templates"
    )
    op.execute("DROP TABLE IF EXISTS campaign_email_templates CASCADE")
