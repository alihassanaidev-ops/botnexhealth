"""Prevent ambiguous NexHealth tenant and location mappings.

NexHealth subdomains identify an institution and location ids identify a site
within that subdomain. The database must not allow the same mapping to belong
to two institutions, because webhook resolution otherwise has no safe winner.
"""

from __future__ import annotations

from alembic import op


revision = "20260904_nh_mapping_security"
down_revision = "20260903_nh_webhook_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A subdomain may have several locations, but it must belong to only one
    # institution. This trigger preserves the valid multi-location case while
    # rejecting cross-tenant reuse.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_nexhealth_subdomain_institution()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.nexhealth_subdomain IS NOT NULL AND EXISTS (
                SELECT 1
                FROM institution_locations existing
                WHERE existing.nexhealth_subdomain = NEW.nexhealth_subdomain
                  AND existing.institution_id <> NEW.institution_id
                  AND existing.id <> NEW.id
            ) THEN
                RAISE EXCEPTION
                    'NexHealth subdomain % is already bound to another institution',
                    NEW.nexhealth_subdomain
                    USING ERRCODE = 'unique_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS institution_locations_nexhealth_subdomain_guard
        ON institution_locations;
        """
    )
    op.execute(
        """
        CREATE TRIGGER institution_locations_nexhealth_subdomain_guard
        BEFORE INSERT OR UPDATE OF institution_id, nexhealth_subdomain
        ON institution_locations
        FOR EACH ROW
        EXECUTE FUNCTION enforce_nexhealth_subdomain_institution();
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            uq_institution_locations_nexhealth_mapping
        ON institution_locations (nexhealth_subdomain, nexhealth_location_id)
        WHERE nexhealth_subdomain IS NOT NULL
          AND nexhealth_location_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS institution_locations_nexhealth_subdomain_guard
        ON institution_locations;
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS enforce_nexhealth_subdomain_institution();
        """
    )
    op.execute(
        """
        DROP INDEX IF EXISTS uq_institution_locations_nexhealth_mapping;
        """
    )
