"""Per-clinic email sending identities and their verification state.

A from-address only delivers if the domain behind it is authenticated with the
sending provider. An unverified domain does not bounce loudly, it lands in spam,
so verification state is stored and enforced rather than assumed.

Rows are scoped to an institution (``location_id IS NULL``) or to one location.
Two partial unique indexes rather than one composite: Postgres treats NULLs as
distinct, so a plain UNIQUE (institution_id, location_id) would happily allow
several institution-wide rows.

Revision ID: 20260825_email_identities
Revises: 20260825_campaign_email_tpl
"""

from __future__ import annotations

from alembic import op


revision = "20260825_email_identities"
down_revision = "20260825_campaign_email_tpl"
branch_labels = None
depends_on = None


# Institution-owned RLS policy, matching sms_templates / campaign_email_templates.
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
        CREATE TABLE IF NOT EXISTS email_sending_identities (
            id                          uuid PRIMARY KEY,
            institution_id              uuid NOT NULL
                                        REFERENCES institutions(id) ON DELETE CASCADE,
            location_id                 uuid
                                        REFERENCES institution_locations(id) ON DELETE CASCADE,
            provider                    varchar(24) NOT NULL DEFAULT 'ses',
            domain                      varchar(255) NOT NULL,
            from_address                varchar(320) NOT NULL,
            from_name                   varchar(255),
            reply_to_address            varchar(320),
            status                      varchar(24) NOT NULL DEFAULT 'pending_dns',
            dns_records                 jsonb,
            provider_tenant_name        varchar(64),
            provider_configuration_set  varchar(64),
            last_checked_at             timestamptz,
            verified_at                 timestamptz,
            failure_reason              text,
            created_at                  timestamptz NOT NULL DEFAULT now(),
            updated_at                  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_email_sending_identities_provider
                CHECK (provider IN ('ses', 'resend')),
            CONSTRAINT ck_email_sending_identities_status
                CHECK (status IN ('pending_dns', 'verifying', 'verified', 'failed', 'revoked'))
        )
        """
    )

    # One institution-wide identity...
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_email_sending_identity_institution
        ON email_sending_identities (institution_id)
        WHERE location_id IS NULL
        """
    )
    # ...and at most one per location.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_email_sending_identity_location
        ON email_sending_identities (institution_id, location_id)
        WHERE location_id IS NOT NULL
        """
    )
    # A sending domain must belong to exactly one clinic, or replies and
    # reputation could not be attributed.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_email_sending_identity_domain
        ON email_sending_identities (lower(domain))
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_sending_identity_scope "
        "ON email_sending_identities (institution_id, location_id)"
    )
    # The verification sweep looks for identities due a re-check.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_sending_identity_status_checked
        ON email_sending_identities (status, last_checked_at)
        """
    )

    # ── RLS (FORCE) + owned policy + runtime-role grants ─────────────────
    op.execute("ALTER TABLE email_sending_identities ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE email_sending_identities FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS email_sending_identities_rls ON email_sending_identities"
    )
    op.execute(
        f"""
        CREATE POLICY email_sending_identities_rls ON email_sending_identities FOR ALL
        USING ({_owned("email_sending_identities")})
        WITH CHECK ({_owned("email_sending_identities")})
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON email_sending_identities
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS email_sending_identities_rls ON email_sending_identities"
    )
    op.execute("DROP TABLE IF EXISTS email_sending_identities CASCADE")
