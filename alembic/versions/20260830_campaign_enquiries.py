"""Add the inbound sales enquiry store.

Revision ID: 20260830_campaign_enquiries
Revises: 20260819_provider_hidden
"""

from __future__ import annotations

from alembic import op

revision = "20260830_campaign_enquiries"
down_revision = "20260819_provider_hidden"
branch_labels = None
depends_on = None

TABLE = "campaign_enquiries"


def _rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('twilio', 'retell', 'celery', 'dead_letter')
            AND {table}.institution_id = app_rls_institution_id()
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
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"CREATE POLICY {table}_rls ON {table} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )
    _grant(table)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_enquiries (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id      uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id         uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            intake_key          varchar(160) NOT NULL,
            source              varchar(80)  NOT NULL,
            first_name          varchar(100),
            last_name           varchar(100),
            email_encrypted     text,
            phone_encrypted     text,
            phone_hash          varchar(64),
            status              varchar(32) NOT NULL DEFAULT 'new',
            contact_id          uuid REFERENCES contacts(id) ON DELETE SET NULL,
            workflow_run_id     uuid,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_campaign_enquiries_status CHECK (
                status IN (
                    'new', 'engaged', 'qualified', 'not_qualified',
                    'unreachable', 'booked', 'handed_to_staff'
                )
            )
        )
        """
    )
    # Re-submitting the same enquiry must not enrol the person twice. Scoped per
    # institution so two clinics cannot collide on a shared key.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_enquiries_intake_key "
        "ON campaign_enquiries (institution_id, intake_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_campaign_enquiries_institution_status "
        "ON campaign_enquiries (institution_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_campaign_enquiries_institution_phone "
        "ON campaign_enquiries (institution_id, phone_hash)"
    )
    _enable_rls(TABLE)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
