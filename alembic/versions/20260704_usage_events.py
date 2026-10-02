"""Usage-metering ingestion table (Plan 11 core).

Adds the ``usage_events`` table that captures per-interaction consumption
(SMS segments/cost, email sends, voice minutes) so cost rollup and analytics
have a durable data source. Rows are append-style billing signals deduped by a
per-institution ``idempotency_key`` so replayed provider webhooks never
double-count. Enables + forces RLS with the same institution/location-scoped
policy pattern as the automation workflow engine.

Revision ID: 20260704_usage_events
Revises: 20260703_provisioning
"""

from __future__ import annotations

from alembic import op


revision = "20260704_usage_events"
down_revision = "20260703_provisioning"
branch_labels = None
depends_on = None


TABLE = "usage_events"


def _rls_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter', 'usage_metering')
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
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id       uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id          uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            channel              varchar(20) NOT NULL,
            direction            varchar(20) NOT NULL DEFAULT 'outbound',
            provider             varchar(30) NOT NULL,
            segments             integer,
            minutes              numeric(12, 4),
            dials                integer,
            emails               integer,
            cost_amount          numeric(12, 5),
            currency             varchar(3) NOT NULL DEFAULT 'USD',
            provider_message_id  varchar(200),
            external_ref         varchar(200),
            idempotency_key      varchar(200),
            occurred_at          timestamptz NOT NULL DEFAULT now(),
            created_at           timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_usage_events_channel
                CHECK (channel IN ('sms', 'email', 'voice')),
            CONSTRAINT ck_usage_events_direction
                CHECK (direction IN ('outbound', 'inbound')),
            CONSTRAINT ck_usage_events_provider
                CHECK (provider IN ('twilio', 'resend', 'retell'))
        )
        """
    )

    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_usage_events_institution_id "
        "ON usage_events (institution_id)",
        "CREATE INDEX IF NOT EXISTS ix_usage_events_location_id "
        "ON usage_events (location_id)",
        "CREATE INDEX IF NOT EXISTS ix_usage_events_institution_occurred "
        "ON usage_events (institution_id, occurred_at)",
        "CREATE INDEX IF NOT EXISTS ix_usage_events_channel "
        "ON usage_events (channel)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_events_idempotency "
        "ON usage_events (institution_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL",
    ):
        op.execute(stmt)

    expr = _rls_expr(TABLE)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute(
        f"""
        CREATE POLICY {TABLE}_rls ON {TABLE} FOR ALL
        USING ({expr})
        WITH CHECK ({expr})
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON usage_events TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute("DROP TABLE IF EXISTS usage_events")
