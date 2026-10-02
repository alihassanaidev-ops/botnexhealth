"""Normalize clinic email domains from their selectable sender addresses.

Revision ID: 20260902_multi_domain_email
Revises: 20260902_merge_heads
"""

from alembic import op


revision = "20260902_multi_domain_email"
down_revision = "20260902_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The identity row now represents a provider domain owned by an institution,
    # not the one address selected for a scope.
    op.execute("DROP INDEX IF EXISTS uq_email_sending_identity_institution")
    op.execute("DROP INDEX IF EXISTS uq_email_sending_identity_location")
    op.execute(
        "ALTER TABLE email_sending_identities "
        "ADD COLUMN IF NOT EXISTS inbound_domain varchar(255)"
    )
    op.execute(
        "ALTER TABLE email_sending_identities "
        "ADD COLUMN IF NOT EXISTS inbound_dns_records jsonb"
    )
    op.execute(
        "ALTER TABLE email_sending_identities "
        "ADD COLUMN IF NOT EXISTS inbound_enabled boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE email_sending_identities "
        "ADD COLUMN IF NOT EXISTS dns_managed boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_email_sending_identity_inbound_domain "
        "ON email_sending_identities (lower(inbound_domain)) "
        "WHERE inbound_domain IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS email_sender_addresses (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            email_identity_id uuid NOT NULL
                REFERENCES email_sending_identities(id) ON DELETE CASCADE,
            location_id uuid REFERENCES institution_locations(id) ON DELETE CASCADE,
            local_part varchar(64) NOT NULL,
            from_address varchar(320) NOT NULL,
            from_name varchar(255),
            external_reply_to varchar(320),
            is_active boolean NOT NULL DEFAULT true,
            is_default boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_email_sender_addresses_local_part
                CHECK (local_part ~ '^[a-z0-9._-]+$')
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_email_sender_addresses_address "
        "ON email_sender_addresses (lower(from_address))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_sender_addresses_institution "
        "ON email_sender_addresses (institution_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_sender_addresses_domain "
        "ON email_sender_addresses (email_identity_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_email_sender_addresses_institution_default "
        "ON email_sender_addresses (institution_id) "
        "WHERE location_id IS NULL AND is_default"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_email_sender_addresses_location_default "
        "ON email_sender_addresses (institution_id, location_id) "
        "WHERE location_id IS NOT NULL AND is_default"
    )
    op.execute(
        """
        INSERT INTO email_sender_addresses (
            institution_id, email_identity_id, location_id, local_part,
            from_address, from_name, external_reply_to, is_active, is_default,
            created_at, updated_at
        )
        SELECT institution_id, id, location_id,
               split_part(from_address, '@', 1), from_address, from_name,
               reply_to_address, true, true, created_at, updated_at
          FROM email_sending_identities
        ON CONFLICT DO NOTHING
        """
    )

    op.execute(
        "ALTER TABLE email_inbox_settings ADD COLUMN IF NOT EXISTS "
        "email_identity_id uuid REFERENCES email_sending_identities(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_inbox_settings_identity "
        "ON email_inbox_settings (email_identity_id)"
    )
    op.execute(
        "ALTER TABLE outbound_email_messages ADD COLUMN IF NOT EXISTS "
        "sender_address_id uuid REFERENCES email_sender_addresses(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbound_email_messages_sender_address "
        "ON outbound_email_messages (sender_address_id)"
    )

    op.execute("ALTER TABLE email_sender_addresses ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE email_sender_addresses FORCE ROW LEVEL SECURITY")
    for operation in ("select", "insert", "update", "delete"):
        op.execute(
            f"DROP POLICY IF EXISTS email_sender_addresses_rls_{operation} "
            "ON email_sender_addresses"
        )
    global_access = """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter', 'retell', 'twilio')
            AND email_sender_addresses.institution_id = app_rls_institution_id()
        )
    """
    read_access = f"""
        {global_access}
        OR (
            app_rls_context_type() = 'user'
            AND email_sender_addresses.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR email_sender_addresses.location_id IS NULL
                OR email_sender_addresses.location_id = app_rls_location_id()
            )
        )
    """
    write_access = f"""
        {global_access}
        OR (
            app_rls_context_type() = 'user'
            AND email_sender_addresses.institution_id = app_rls_institution_id()
            AND app_rls_role() = 'INSTITUTION_ADMIN'
        )
    """
    op.execute(
        "CREATE POLICY email_sender_addresses_rls_select ON email_sender_addresses "
        f"FOR SELECT USING ({read_access})"
    )
    op.execute(
        "CREATE POLICY email_sender_addresses_rls_insert ON email_sender_addresses "
        f"FOR INSERT WITH CHECK ({write_access})"
    )
    op.execute(
        "CREATE POLICY email_sender_addresses_rls_update ON email_sender_addresses "
        f"FOR UPDATE USING ({write_access}) WITH CHECK ({write_access})"
    )
    op.execute(
        "CREATE POLICY email_sender_addresses_rls_delete ON email_sender_addresses "
        f"FOR DELETE USING ({write_access})"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON email_sender_addresses TO nexhealth_app;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE outbound_email_messages DROP COLUMN IF EXISTS sender_address_id"
    )
    op.execute(
        "ALTER TABLE email_inbox_settings DROP COLUMN IF EXISTS email_identity_id"
    )
    op.execute("DROP TABLE IF EXISTS email_sender_addresses CASCADE")
    op.execute(
        "DROP INDEX IF EXISTS uq_email_sending_identity_inbound_domain"
    )
    op.execute(
        "ALTER TABLE email_sending_identities DROP COLUMN IF EXISTS inbound_enabled"
    )
    op.execute(
        "ALTER TABLE email_sending_identities DROP COLUMN IF EXISTS dns_managed"
    )
    op.execute(
        "ALTER TABLE email_sending_identities DROP COLUMN IF EXISTS inbound_dns_records"
    )
    op.execute(
        "ALTER TABLE email_sending_identities DROP COLUMN IF EXISTS inbound_domain"
    )
    # Refuse a lossy downgrade after more than one domain has been configured
    # for a scope; an operator must choose which domain to retain first.
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM email_sending_identities
            GROUP BY institution_id, location_id HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade: multiple email domains exist for a scope';
          END IF;
        END $$
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_email_sending_identity_institution "
        "ON email_sending_identities (institution_id) WHERE location_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_email_sending_identity_location "
        "ON email_sending_identities (institution_id, location_id) "
        "WHERE location_id IS NOT NULL"
    )
