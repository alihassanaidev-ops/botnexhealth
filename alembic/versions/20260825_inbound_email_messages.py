"""Inbound email messages, and widen conversation threads to carry email.

``campaign_conversation_threads.channel`` is a generic column but its CHECK
constraint was written as ``channel IN ('sms')``, so an email thread would be
rejected at insert. Widening it here is what lets the shared inbox show SMS and
email conversations on one table instead of a parallel stack.

``inbound_email_messages`` mirrors ``inbound_sms_messages``: hashed and masked
addresses, encrypted body, nothing PHI-bearing in clear. Email adds an encrypted
subject (subjects carry PHI too), RFC 5322 threading headers, and the provider's
spam/virus/authentication verdicts.

``institution_id`` is nullable here, unlike the SMS table. Inbound arrives on a
catch-all address, so mail can turn up that we cannot attribute to a tenant.
Holding it unattributed is correct; guessing would be a cross-tenant disclosure.
Those rows are visible only to super admins under the RLS policy below.

Revision ID: 20260825_inbound_email
Revises: 20260825_email_identities
"""

from __future__ import annotations

from alembic import op


revision = "20260825_inbound_email"
down_revision = "20260825_email_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Let conversation threads carry email ─────────────────────────────
    op.execute(
        "ALTER TABLE campaign_conversation_threads "
        "DROP CONSTRAINT IF EXISTS ck_campaign_conversation_threads_channel"
    )
    op.execute(
        "ALTER TABLE campaign_conversation_threads "
        "ADD CONSTRAINT ck_campaign_conversation_threads_channel "
        "CHECK (channel IN ('sms', 'email'))"
    )

    # ── Inbound email log ────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS inbound_email_messages (
            id                      uuid PRIMARY KEY,
            institution_id          uuid REFERENCES institutions(id) ON DELETE CASCADE,
            location_id             uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            contact_id              uuid REFERENCES contacts(id) ON DELETE SET NULL,
            workflow_run_id         uuid,
            conversation_thread_id  uuid REFERENCES campaign_conversation_threads(id) ON DELETE SET NULL,
            provider_message_id     varchar(255) UNIQUE,
            storage_key             varchar(512),
            from_email_hash         varchar(64),
            from_email_masked       varchar(255),
            to_email_masked         varchar(255),
            message_id              varchar(512),
            in_reply_to             varchar(512),
            "references"            text,
            intent                  varchar(24) NOT NULL DEFAULT 'free_text',
            status                  varchar(24) NOT NULL DEFAULT 'routed',
            status_reason           varchar(255),
            subject_encrypted       text,
            body_encrypted          text,
            has_attachments         boolean NOT NULL DEFAULT false,
            attachment_count        integer NOT NULL DEFAULT 0,
            spam_verdict            varchar(24),
            virus_verdict           varchar(24),
            spf_verdict             varchar(24),
            dkim_verdict            varchar(24),
            dmarc_verdict           varchar(24),
            sender_mismatch         boolean NOT NULL DEFAULT false,
            received_at             timestamptz,
            created_at              timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_inbound_email_messages_status
                CHECK (status IN ('routed', 'unroutable', 'quarantined'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_institution_created "
        "ON inbound_email_messages (institution_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_contact "
        "ON inbound_email_messages (contact_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_thread "
        "ON inbound_email_messages (conversation_thread_id)"
    )
    # Threading fallback when a reply arrives without our routing token.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_in_reply_to "
        "ON inbound_email_messages (in_reply_to)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_message_id "
        "ON inbound_email_messages (message_id)"
    )
    # Sender lookup for the "new email, no token" path, and for rate limiting a
    # sender that is flooding the catch-all.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_email_messages_from_hash "
        "ON inbound_email_messages (from_email_hash, created_at DESC)"
    )

    # ── RLS ──────────────────────────────────────────────────────────────
    # Same institution-owned shape as the rest, with one addition: rows whose
    # institution_id is NULL (unattributable mail) are visible to super admins
    # and system contexts only. A tenant must never see mail we could not place.
    op.execute("ALTER TABLE inbound_email_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE inbound_email_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS inbound_email_messages_rls ON inbound_email_messages"
    )
    op.execute(
        """
        CREATE POLICY inbound_email_messages_rls ON inbound_email_messages FOR ALL
        USING (
            app_rls_is_super_admin()
            OR app_rls_context_type() IN ('celery', 'dead_letter')
            OR (
                inbound_email_messages.institution_id IS NOT NULL
                AND inbound_email_messages.institution_id = app_rls_institution_id()
            )
        )
        WITH CHECK (
            app_rls_is_super_admin()
            OR app_rls_context_type() IN ('celery', 'dead_letter')
            OR (
                inbound_email_messages.institution_id IS NOT NULL
                AND inbound_email_messages.institution_id = app_rls_institution_id()
            )
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON inbound_email_messages
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS inbound_email_messages_rls ON inbound_email_messages"
    )
    op.execute("DROP TABLE IF EXISTS inbound_email_messages CASCADE")
    op.execute(
        "ALTER TABLE campaign_conversation_threads "
        "DROP CONSTRAINT IF EXISTS ck_campaign_conversation_threads_channel"
    )
    op.execute(
        "ALTER TABLE campaign_conversation_threads "
        "ADD CONSTRAINT ck_campaign_conversation_threads_channel "
        "CHECK (channel IN ('sms'))"
    )
