"""Connected form providers, synced forms, field mappings and submissions.

The clinic-facing half of this is four ordinary institution-scoped tables. The
part worth reading is the RLS, because inbound form webhooks arrive with no
session and no tenant, and each provider hands us a different thing to resolve
the tenant *from*:

* Meta posts every app's leads to one platform URL and names the clinic only by
  the Page id inside the body. So a connection row has to be findable by
  ``(provider, account_ref)`` before any institution is known.
* Typeform posts to a per-form URL we chose at registration, so the form row id
  is in the path — findable by id, again before any institution is known.

Both get the same treatment the intake token already gets: a SELECT-only lookup
context that can see exactly the one row the request names, and nothing else.
The resolved row then supplies the institution for a second, ordinary
institution-scoped context that does the real work.

New policies are *added* alongside the existing ones rather than rewriting them.
Postgres ORs permissive policies together, so the narrow addition says exactly
what the new contexts may do and leaves the long-standing policies untouched.

Revision ID: 20260902_form_integrations
Revises: 20260902_email_identity_activation
"""

from __future__ import annotations

from alembic import op

revision = "20260902_form_integrations"
down_revision = "20260902_email_identity_activation"
branch_labels = None
depends_on = None


#: Full tenant context for a verified webhook, after the provider payload has
#: been resolved to one institution.
WEBHOOK_CONTEXT = "form_webhook"

#: Pre-tenant resolution only. Can SELECT the single connection or form row the
#: request identifies, and nothing else anywhere.
LOOKUP_CONTEXT = "form_webhook_lookup"

OWNED_TABLES = (
    "form_provider_connections",
    "form_definitions",
    "form_field_mappings",
    "form_submissions",
)

#: What landing a submission touches beyond its own tables: the contact it
#: creates or matches, the consent the form captured, and the custom field
#: values a mapped question writes.
WEBHOOK_WRITE_TABLES = (
    "contacts",
    "contact_location_accesses",
    "consent_records",
    "custom_field_values",
)

#: Read-only for the webhook: the field definitions a mapping points at, and the
#: workflows a landed submission may enroll.
WEBHOOK_READ_TABLES = (
    "custom_field_definitions",
    "automation_workflows",
    "automation_workflow_versions",
)


def _admin_policy(table: str) -> str:
    """Clinic administrators manage their own institution's rows.

    Deliberately admin-only rather than any signed-in user: these rows hold a
    provider access token and decide where a stranger's contact details land.
    """
    return f"""
        CREATE POLICY {table}_rls ON {table} FOR ALL
        USING (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() IN ('celery', 'dead_letter')
                AND institution_id = app_rls_institution_id()
            )
            OR (
                app_rls_context_type() = 'user'
                AND institution_id = app_rls_institution_id()
                AND app_rls_role() IN ('INSTITUTION_ADMIN', 'GROUP_ADMIN')
            )
        )
        WITH CHECK (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() IN ('celery', 'dead_letter')
                AND institution_id = app_rls_institution_id()
            )
            OR (
                app_rls_context_type() = 'user'
                AND institution_id = app_rls_institution_id()
                AND app_rls_role() IN ('INSTITUTION_ADMIN', 'GROUP_ADMIN')
            )
        )
    """


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS form_provider_connections (
            id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id          uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            provider                varchar(20) NOT NULL,
            account_ref             varchar(120) NOT NULL,
            account_name            varchar(200),
            access_token_encrypted  text,
            refresh_token_encrypted text,
            token_expires_at        timestamptz,
            granted_scopes          varchar(500),
            status                  varchar(20) NOT NULL DEFAULT 'active',
            last_error              varchar(500),
            last_synced_at          timestamptz,
            created_by_user_id      uuid,
            -- Set instead of deleting the row. Disconnecting must not take the
            -- record of who came in with it: the forms, their field maps and
            -- every landed submission hang off this id.
            disconnected_at         timestamptz,
            created_at              timestamptz NOT NULL DEFAULT now(),
            updated_at              timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_form_provider_connections_provider
                CHECK (provider IN ('meta', 'typeform')),
            CONSTRAINT ck_form_provider_connections_status
                CHECK (status IN ('active', 'needs_reauth', 'revoked')),
            CONSTRAINT uq_form_provider_connections_account
                UNIQUE (institution_id, provider, account_ref)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_form_provider_connections_institution "
        "ON form_provider_connections (institution_id, provider)"
    )
    # The Meta webhook's only handle on the tenant.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_form_provider_connections_account_ref "
        "ON form_provider_connections (provider, account_ref)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS form_definitions (
            id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id          uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            connection_id           uuid NOT NULL REFERENCES form_provider_connections(id) ON DELETE CASCADE,
            provider                varchar(20) NOT NULL,
            external_form_id        varchar(160) NOT NULL,
            name                    varchar(300) NOT NULL,
            location_id             uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            fields                  jsonb,
            is_enabled              boolean NOT NULL DEFAULT false,
            source_name             varchar(80) NOT NULL DEFAULT 'external_form',
            consent_sms             boolean NOT NULL DEFAULT false,
            consent_email           boolean NOT NULL DEFAULT false,
            consent_wording         text,
            webhook_status          varchar(20) NOT NULL DEFAULT 'none',
            webhook_registered_at   timestamptz,
            webhook_secret_encrypted text,
            webhook_last_error      varchar(500),
            last_submission_at      timestamptz,
            last_synced_at          timestamptz,
            archived_at             timestamptz,
            created_at              timestamptz NOT NULL DEFAULT now(),
            updated_at              timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_form_definitions_provider
                CHECK (provider IN ('meta', 'typeform')),
            CONSTRAINT ck_form_definitions_webhook_status
                CHECK (webhook_status IN ('none', 'registered', 'failed')),
            CONSTRAINT uq_form_definitions_external
                UNIQUE (institution_id, provider, external_form_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_form_definitions_institution "
        "ON form_definitions (institution_id, provider)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_form_definitions_connection "
        "ON form_definitions (connection_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS form_field_mappings (
            id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id          uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            form_id                 uuid NOT NULL REFERENCES form_definitions(id) ON DELETE CASCADE,
            source_key              varchar(200) NOT NULL,
            source_label            varchar(500),
            source_type             varchar(60),
            target_kind             varchar(20) NOT NULL DEFAULT 'ignore',
            target_contact_field    varchar(60),
            target_custom_field_id  uuid REFERENCES custom_field_definitions(id) ON DELETE CASCADE,
            context_key             varchar(120),
            created_at              timestamptz NOT NULL DEFAULT now(),
            updated_at              timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_form_field_mappings_target_kind
                CHECK (target_kind IN ('contact_field', 'custom_field', 'ignore')),
            -- A mapping has to name what it maps onto. Without this a row can
            -- claim a target kind and silently point at nothing, which reads as
            -- "mapped" in the UI and drops the answer at runtime.
            CONSTRAINT ck_form_field_mappings_target_present CHECK (
                (target_kind = 'contact_field' AND target_contact_field IS NOT NULL)
                OR (target_kind = 'custom_field' AND target_custom_field_id IS NOT NULL)
                OR target_kind = 'ignore'
            ),
            CONSTRAINT uq_form_field_mappings_source UNIQUE (form_id, source_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_form_field_mappings_form "
        "ON form_field_mappings (form_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS form_submissions (
            id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id          uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id             uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            form_id                 uuid NOT NULL REFERENCES form_definitions(id) ON DELETE CASCADE,
            external_submission_id  varchar(200) NOT NULL,
            contact_id              uuid REFERENCES contacts(id) ON DELETE SET NULL,
            context_answers         jsonb,
            raw_payload_encrypted   text,
            raw_retain_until        timestamptz,
            status                  varchar(20) NOT NULL DEFAULT 'received',
            error_summary           varchar(500),
            submitted_at            timestamptz,
            received_at             timestamptz NOT NULL DEFAULT now(),
            -- 'dropped' is a submission we deliberately did not process — the
            -- form is switched off, or nothing on it maps to a contact method.
            -- Distinct from 'failed', which is something breaking. Both are
            -- recorded rather than logged, because a lead nobody can see was
            -- lost is the failure mode that actually costs a clinic money.
            CONSTRAINT ck_form_submissions_status
                CHECK (status IN ('received', 'processed', 'failed', 'dropped')),
            -- The provider's own id for the response is what makes a
            -- redelivered webhook land once instead of twice.
            CONSTRAINT uq_form_submissions_external
                UNIQUE (institution_id, form_id, external_submission_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_form_submissions_form "
        "ON form_submissions (form_id, received_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_form_submissions_contact "
        "ON form_submissions (contact_id)"
    )

    for table in OWNED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
        op.execute(_admin_policy(table))
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

    # A form's name and its field map are not secrets, and the workflow
    # builder has to show them to whoever is editing a workflow — including a
    # location admin, who is not an institution admin. Read is widened to any
    # signed-in user of the owning institution; writing stays with the admins,
    # since that is what decides where a stranger's details land.
    for table in ("form_definitions", "form_field_mappings"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_select ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_select ON {table} FOR SELECT
            USING (
                app_rls_context_type() = 'user'
                AND institution_id = app_rls_institution_id()
            )
            """
        )

    # ── Pre-tenant lookup ────────────────────────────────────────────────
    # Meta names the clinic by Page id; the external id carried on the context
    # is that Page id, and it can match nothing else.
    op.execute(
        "DROP POLICY IF EXISTS form_provider_connections_webhook_lookup "
        "ON form_provider_connections"
    )
    op.execute(
        f"""
        CREATE POLICY form_provider_connections_webhook_lookup
        ON form_provider_connections FOR SELECT
        USING (
            app_rls_context_type() = '{LOOKUP_CONTEXT}'
            AND account_ref = app_rls_external_id()
        )
        """
    )
    # Typeform posts to a per-form URL, so the form row id is in the path.
    op.execute(
        "DROP POLICY IF EXISTS form_definitions_webhook_lookup ON form_definitions"
    )
    op.execute(
        f"""
        CREATE POLICY form_definitions_webhook_lookup
        ON form_definitions FOR SELECT
        USING (
            app_rls_context_type() = '{LOOKUP_CONTEXT}'
            AND id::text = app_rls_external_id()
        )
        """
    )

    # ── Verified webhook, tenant resolved ────────────────────────────────
    for table in (*OWNED_TABLES, *WEBHOOK_WRITE_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_form_webhook ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_form_webhook ON {table} FOR ALL
            USING (
                app_rls_context_type() = '{WEBHOOK_CONTEXT}'
                AND institution_id = app_rls_institution_id()
            )
            WITH CHECK (
                app_rls_context_type() = '{WEBHOOK_CONTEXT}'
                AND institution_id = app_rls_institution_id()
            )
            """
        )
    for table in WEBHOOK_READ_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_form_webhook_select ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_form_webhook_select ON {table} FOR SELECT
            USING (
                app_rls_context_type() = '{WEBHOOK_CONTEXT}'
                AND institution_id = app_rls_institution_id()
            )
            """
        )


def downgrade() -> None:
    for table in ("form_definitions", "form_field_mappings"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_select ON {table}")
    for table in WEBHOOK_READ_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_form_webhook_select ON {table}")
    for table in (*OWNED_TABLES, *WEBHOOK_WRITE_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_form_webhook ON {table}")
    op.execute(
        "DROP POLICY IF EXISTS form_definitions_webhook_lookup ON form_definitions"
    )
    op.execute(
        "DROP POLICY IF EXISTS form_provider_connections_webhook_lookup "
        "ON form_provider_connections"
    )
    for table in OWNED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute("DROP TABLE IF EXISTS form_submissions")
    op.execute("DROP TABLE IF EXISTS form_field_mappings")
    op.execute("DROP TABLE IF EXISTS form_definitions")
    op.execute("DROP TABLE IF EXISTS form_provider_connections")
