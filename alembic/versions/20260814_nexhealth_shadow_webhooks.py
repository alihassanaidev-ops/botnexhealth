"""NexHealth v3 shadow webhook capture.

Revision ID: 20260814_nh_shadow_webhooks
Revises: 20260812_postop_flow_state
"""

from __future__ import annotations

from alembic import op

revision = "20260814_nh_shadow_webhooks"
down_revision = "20260812_postop_flow_state"
branch_labels = None
depends_on = None

EVENTS_TABLE = "nexhealth_webhook_shadow_events"
SUBSCRIPTIONS_TABLE = "nexhealth_webhook_shadow_subscriptions"
# Static RLS coverage guards scan migration source for literal policy names.
# Keep these names visible even though the DDL below uses constants:
# nexhealth_webhook_shadow_events_rls
# nexhealth_webhook_shadow_subscriptions_rls


def _owned_rls_expr(table: str) -> str:
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


def _apply_rls(table: str, expr: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"CREATE POLICY {table}_rls ON {table} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )
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


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
            id uuid PRIMARY KEY,
            institution_id uuid NULL REFERENCES institutions(id) ON DELETE SET NULL,
            location_id uuid NULL REFERENCES institution_locations(id) ON DELETE SET NULL,
            api_contract varchar(32) NOT NULL DEFAULT 'stable_v3',
            route_family varchar(32) NOT NULL,
            subdomain varchar(160) NULL,
            nexhealth_location_id varchar(160) NULL,
            resource_type varchar(80) NULL,
            event_name varchar(160) NULL,
            event_family varchar(160) NULL,
            pms_resource_id varchar(160) NULL,
            change_marker varchar(300) NULL,
            business_event_key varchar(500) NULL,
            provider_delivery_id varchar(160) NULL,
            provider_subscription_id varchar(160) NULL,
            payload_hash varchar(128) NOT NULL,
            parse_status varchar(32) NOT NULL DEFAULT 'parsed',
            parse_error_summary text NULL,
            resolution_status varchar(32) NOT NULL DEFAULT 'unresolved',
            resolution_metadata jsonb NULL,
            extracted_identity jsonb NULL,
            redacted_payload_encrypted text NULL,
            raw_payload_encrypted text NULL,
            raw_payload_retain_until timestamptz NULL,
            raw_payload_purged_at timestamptz NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_institution_id "
        f"ON {EVENTS_TABLE} (institution_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_location_id "
        f"ON {EVENTS_TABLE} (location_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_route_family "
        f"ON {EVENTS_TABLE} (route_family)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_subdomain "
        f"ON {EVENTS_TABLE} (subdomain)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_nexhealth_location_id "
        f"ON {EVENTS_TABLE} (nexhealth_location_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_pms_resource_id "
        f"ON {EVENTS_TABLE} (pms_resource_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_business_event_key "
        f"ON {EVENTS_TABLE} (business_event_key)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_provider_delivery_id "
        f"ON {EVENTS_TABLE} (provider_delivery_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_provider_subscription_id "
        f"ON {EVENTS_TABLE} (provider_subscription_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_payload_hash "
        f"ON {EVENTS_TABLE} (payload_hash)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_parse_status "
        f"ON {EVENTS_TABLE} (parse_status, created_at)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_resource "
        f"ON {EVENTS_TABLE} (resource_type, event_name)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_resolution "
        f"ON {EVENTS_TABLE} (institution_id, location_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_raw_retain "
        f"ON {EVENTS_TABLE} (raw_payload_retain_until)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{EVENTS_TABLE}_raw_purged "
        f"ON {EVENTS_TABLE} (raw_payload_purged_at)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SUBSCRIPTIONS_TABLE} (
            id uuid PRIMARY KEY,
            institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id uuid NOT NULL REFERENCES institution_locations(id) ON DELETE CASCADE,
            route_family varchar(32) NOT NULL,
            api_contract varchar(32) NOT NULL DEFAULT 'stable_v3',
            subdomain varchar(160) NOT NULL,
            nexhealth_location_id varchar(160) NOT NULL,
            callback_url varchar(500) NULL,
            event_types jsonb NOT NULL DEFAULT '[]'::jsonb,
            provider_endpoint_id varchar(160) NULL,
            provider_subscription_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            status varchar(32) NOT NULL DEFAULT 'pending',
            last_health_check_at timestamptz NULL,
            last_event_at timestamptz NULL,
            last_parse_success_at timestamptz NULL,
            last_parse_failure_at timestamptz NULL,
            last_shadow_capture_id uuid NULL,
            parse_success_count integer NOT NULL DEFAULT 0,
            parse_failure_count integer NOT NULL DEFAULT 0,
            error_metadata jsonb NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_nexhealth_webhook_shadow_subscription_route
                UNIQUE (institution_id, location_id, route_family)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{SUBSCRIPTIONS_TABLE}_institution_id "
        f"ON {SUBSCRIPTIONS_TABLE} (institution_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{SUBSCRIPTIONS_TABLE}_location_id "
        f"ON {SUBSCRIPTIONS_TABLE} (location_id)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{SUBSCRIPTIONS_TABLE}_route_family "
        f"ON {SUBSCRIPTIONS_TABLE} (route_family)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{SUBSCRIPTIONS_TABLE}_status "
        f"ON {SUBSCRIPTIONS_TABLE} (institution_id, status)"
    )

    _apply_rls(EVENTS_TABLE, _owned_rls_expr(EVENTS_TABLE))
    _apply_rls(SUBSCRIPTIONS_TABLE, _owned_rls_expr(SUBSCRIPTIONS_TABLE))


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {SUBSCRIPTIONS_TABLE}_rls ON {SUBSCRIPTIONS_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {SUBSCRIPTIONS_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS {EVENTS_TABLE}_rls ON {EVENTS_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {EVENTS_TABLE}")
