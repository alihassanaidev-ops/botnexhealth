"""NexHealth webhook subscription lifecycle table (Plan 09).

Revision ID: 20260708_nh_webhook_subs
Revises: 20260707_appt_working_set
"""

from __future__ import annotations

from alembic import op

revision = "20260708_nh_webhook_subs"
down_revision = "20260707_appt_working_set"
branch_labels = None
depends_on = None

TABLE = "nexhealth_webhook_subscriptions"


def _rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR app_rls_context_type() IN ('celery', 'dead_letter')
        OR (
            app_rls_context_type() IN ('nexhealth_webhooks', 'nexhealth_lookup')
            AND {table}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
        )
    """


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id                        uuid PRIMARY KEY,
            institution_id            uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id               uuid NOT NULL REFERENCES institution_locations(id) ON DELETE CASCADE,
            subdomain                 varchar(160) NOT NULL,
            nexhealth_location_id      varchar(160) NOT NULL,
            event_types               jsonb NOT NULL DEFAULT '[]'::jsonb,
            provider_subscription_id  varchar(160),
            status                    varchar(32) NOT NULL DEFAULT 'pending',
            last_health_check_at      timestamptz,
            last_event_at             timestamptz,
            last_backfill_at          timestamptz,
            last_reconciliation_at    timestamptz,
            error_metadata            jsonb,
            created_at                timestamptz NOT NULL DEFAULT now(),
            updated_at                timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_nexhealth_webhook_subscription_location
                UNIQUE (institution_id, location_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_institution ON {TABLE} (institution_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_location ON {TABLE} (location_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_status ON {TABLE} (institution_id, status)"
    )

    expr = _rls_expr(TABLE)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute(
        f"CREATE POLICY {TABLE}_rls ON {TABLE} FOR ALL USING ({expr}) WITH CHECK ({expr})"
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


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
