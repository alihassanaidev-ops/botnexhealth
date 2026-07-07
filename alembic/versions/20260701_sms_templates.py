"""Admin-editable SMS templates (Approach B, Phase 1).

Adds the ``sms_templates`` table — the SMS counterpart to ``email_templates`` —
so institutions can customize the transactional texts we send to patients
(rendered from authoritative structured data, not Retell's free-text). Same
institution-owned FORCE RLS shape as email_templates / workflow_statuses.

Revision ID: 20260701_sms_templates
Revises: 20260623_campaign_core
"""

from __future__ import annotations

from alembic import op


revision = "20260701_sms_templates"
down_revision = "20260623_campaign_core"
branch_labels = None
depends_on = None


# Institution-owned RLS policy — verbatim shape of the email_templates /
# workflow_statuses baseline: super admin, system contexts (the Retell/Celery
# workers that send the SMS), and the owning institution's users may read/write
# rows for their own institution.
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
        CREATE TABLE IF NOT EXISTS sms_templates (
            id              uuid PRIMARY KEY,
            institution_id  uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            template_type   varchar(50) NOT NULL,
            name            varchar(255) NOT NULL,
            body            text NOT NULL,
            is_active       boolean NOT NULL DEFAULT true,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_sms_template_institution_type "
        "ON sms_templates (institution_id, template_type)"
    )

    # ── RLS (FORCE) + owned policy + runtime-role grants ─────────────────
    # Literal table name (matches the workflow_statuses migration style) so the
    # policy name is greppable by the RLS-coverage governance test.
    op.execute("ALTER TABLE sms_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sms_templates FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS sms_templates_rls ON sms_templates")
    op.execute(
        f"""
        CREATE POLICY sms_templates_rls ON sms_templates FOR ALL
        USING ({_owned("sms_templates")})
        WITH CHECK ({_owned("sms_templates")})
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON sms_templates TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS sms_templates_rls ON sms_templates")
    op.execute("DROP TABLE IF EXISTS sms_templates CASCADE")
