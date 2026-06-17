"""Allow GROUP_ADMIN to read member institutions' locations (org data, not PHI).

Lets the group oversight role drill into one member practice and see its
per-location dashboard. Read-only; the WITH CHECK stays strict (no writes).
Member data reads still set app.institution_id per-request, so this only
authorizes reading the locations of whichever member is the active scope.

Revision ID: 20260619_group_loc_read
Revises: 20260618_inst_groups
"""

from __future__ import annotations

from alembic import op


revision = "20260619_group_loc_read"
down_revision = "20260618_inst_groups"
branch_labels = None
depends_on = None


# Verbatim baseline institution_locations expression (USING + CHECK).
_LOCATIONS_BASE = """
    app_rls_is_super_admin()
    OR (
        app_rls_context_type() = 'middleware_lookup'
        AND EXISTS (
            SELECT 1 FROM institutions i
            WHERE i.id = institution_locations.institution_id
              AND i.slug = app_rls_external_id()
        )
    )
    OR (
        app_rls_context_type() = 'retell_lookup'
        AND institution_locations.retell_agent_id = app_rls_external_id()
    )
    OR (
        app_rls_context_type() = 'twilio_lookup'
        AND institution_locations.twilio_from_number = app_rls_external_id()
    )
    OR (
        app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
        AND (
            institution_locations.institution_id = app_rls_institution_id()
            OR institution_locations.id = app_rls_location_id()
            OR institution_locations.id::text = app_rls_external_id()
        )
    )
    OR (
        app_rls_context_type() = 'user'
        AND institution_locations.institution_id = app_rls_institution_id()
        AND (
            app_rls_role() = 'INSTITUTION_ADMIN'
            OR institution_locations.id = app_rls_location_id()
        )
    )
"""

_LOCATIONS_GROUP_READ = """
    OR (
        app_rls_context_type() = 'user'
        AND app_rls_role() = 'GROUP_ADMIN'
        AND institution_locations.institution_id = app_rls_institution_id()
    )
"""


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS institution_locations_rls ON institution_locations")
    op.execute(
        f"""
        CREATE POLICY institution_locations_rls ON institution_locations FOR ALL
        USING ({_LOCATIONS_BASE} {_LOCATIONS_GROUP_READ})
        WITH CHECK ({_LOCATIONS_BASE})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS institution_locations_rls ON institution_locations")
    op.execute(
        f"""
        CREATE POLICY institution_locations_rls ON institution_locations FOR ALL
        USING ({_LOCATIONS_BASE})
        WITH CHECK ({_LOCATIONS_BASE})
        """
    )
