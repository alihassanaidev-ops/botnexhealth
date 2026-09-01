"""Finish contact consolidation and close background-worker RLS gaps.

Revision ID: 20260902_contact_rls
Revises: 20260902_enquiry_intake_lookup
"""

from __future__ import annotations

from alembic import op


revision = "20260902_contact_rls"
down_revision = "20260902_enquiry_intake_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve every legacy row before removing the duplicate person store.
    # Linked rows enrich their existing Contact; unlinked rows keep their UUID,
    # encrypted fields, attribution, timestamps, and location visibility.
    op.execute(
        """
        UPDATE contacts AS c
        SET first_name = COALESCE(c.first_name, e.first_name),
            last_name = COALESCE(c.last_name, e.last_name),
            full_name = COALESCE(
                c.full_name,
                NULLIF(trim(concat_ws(' ', e.first_name, e.last_name)), '')
            ),
            email_encrypted = COALESCE(c.email_encrypted, e.email_encrypted),
            phone_encrypted = COALESCE(c.phone_encrypted, e.phone_encrypted),
            phone_hash = COALESCE(c.phone_hash, e.phone_hash),
            email_hash = COALESCE(c.email_hash, e.email_hash),
            lead_source = COALESCE(c.lead_source, e.source),
            lead_status = CASE
                WHEN c.nexhealth_patient_id IS NOT NULL THEN c.lead_status
                ELSE COALESCE(c.lead_status, e.status)
            END,
            intake_key = COALESCE(c.intake_key, e.intake_key),
            attribution = COALESCE(c.attribution, e.attribution),
            external_ref = COALESCE(c.external_ref, e.external_ref),
            notes_encrypted = COALESCE(c.notes_encrypted, e.notes_encrypted),
            updated_at = GREATEST(c.updated_at, e.updated_at)
        FROM campaign_enquiries AS e
        WHERE e.contact_id = c.id
        """
    )
    op.execute(
        """
        INSERT INTO contacts (
            id, institution_id, first_name, last_name, full_name,
            email_encrypted, phone_encrypted, phone_hash, email_hash,
            lead_source, lead_status, intake_key, attribution, external_ref,
            notes_encrypted, is_new_patient, created_at, updated_at
        )
        SELECT
            e.id, e.institution_id, e.first_name, e.last_name,
            NULLIF(trim(concat_ws(' ', e.first_name, e.last_name)), ''),
            e.email_encrypted, e.phone_encrypted, e.phone_hash, e.email_hash,
            e.source, e.status, e.intake_key, e.attribution, e.external_ref,
            e.notes_encrypted, true, e.created_at, e.updated_at
        FROM campaign_enquiries AS e
        WHERE e.contact_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM contacts AS c
              WHERE c.institution_id = e.institution_id
                AND c.intake_key = e.intake_key
          )
        """
    )
    op.execute(
        """
        INSERT INTO contact_location_accesses (
            id, institution_id, contact_id, location_id, created_at
        )
        SELECT DISTINCT ON (c.id, e.location_id)
            gen_random_uuid(), e.institution_id, c.id, e.location_id, e.created_at
        FROM campaign_enquiries AS e
        JOIN contacts AS c
          ON c.institution_id = e.institution_id
         AND (
             c.id = e.contact_id
             OR (e.contact_id IS NULL AND c.id = e.id)
             OR (e.contact_id IS NULL AND c.intake_key = e.intake_key)
         )
        WHERE e.location_id IS NOT NULL
        ON CONFLICT (contact_id, location_id) DO NOTHING
        """
    )
    op.execute("DROP TABLE campaign_enquiries")

    # The signed intake endpoint must be able to read the active workflow and
    # its current version after it creates/matches the Contact. These are
    # SELECT-only policies; intake cannot mutate campaign definitions.
    op.execute(
        """
        CREATE POLICY automation_workflows_enquiry_intake_select
        ON automation_workflows FOR SELECT
        USING (
            app_rls_context_type() = 'enquiry_intake'
            AND institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR location_id IS NULL
                OR location_id = app_rls_location_id()
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY automation_workflow_versions_enquiry_intake_select
        ON automation_workflow_versions FOR SELECT
        USING (
            app_rls_context_type() = 'enquiry_intake'
            AND institution_id = app_rls_institution_id()
        )
        """
    )

    # RLS on a partitioned parent is not inherited by a child named directly.
    # Runtime code only needs the parent grant; revoke legacy direct child
    # access from every current audit partition.
    op.execute(
        """
        DO $$
        DECLARE child regclass;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                FOR child IN
                    SELECT inhrelid::regclass
                    FROM pg_inherits
                    WHERE inhparent = 'audit_logs'::regclass
                LOOP
                    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %s FROM nexhealth_app', child);
                END LOOP;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS automation_workflow_versions_enquiry_intake_select "
        "ON automation_workflow_versions"
    )
    op.execute(
        "DROP POLICY IF EXISTS automation_workflows_enquiry_intake_select "
        "ON automation_workflows"
    )
    # The removed table was already unused. Downgrade recreates its final shape
    # so older application code can start; Contact remains the source of truth.
    op.execute(
        """
        CREATE TABLE campaign_enquiries (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            institution_id uuid NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            location_id uuid REFERENCES institution_locations(id) ON DELETE SET NULL,
            intake_key varchar(160) NOT NULL,
            source varchar(80) NOT NULL,
            first_name varchar(100), last_name varchar(100),
            email_encrypted text, phone_encrypted text,
            phone_hash varchar(64), email_hash varchar(64),
            attribution jsonb, external_ref varchar(160), notes_encrypted text,
            status varchar(32) NOT NULL DEFAULT 'new',
            contact_id uuid REFERENCES contacts(id) ON DELETE SET NULL,
            workflow_run_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (institution_id, intake_key)
        )
        """
    )
    op.execute("ALTER TABLE campaign_enquiries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE campaign_enquiries FORCE ROW LEVEL SECURITY")
