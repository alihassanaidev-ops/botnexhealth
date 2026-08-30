"""Multi-location users: extra location assignments per account.

Additive only. A user's primary location stays on ``users.location_id``;
rows here grant the same account additional locations in the institution.
Existing users have no rows and keep their exact current behavior.

Also recreates ``institution_locations_rls`` so a location-scoped user can
*read* every location they're assigned (the list feeding the frontend's
location selector); acting on one still requires the request to bind that
location into ``app.location_id`` after a membership check. The membership
lookup uses a SECURITY DEFINER helper (plpgsql, BYPASSRLS owner) following
the baseline's pattern — an inline subquery would recurse through the
``user_locations`` policy.

Revision ID: 20260831_user_locations
Revises: 20260830_sms_staff_templates
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260831_user_locations"
down_revision = "20260830_sms_staff_templates"
branch_labels = None
depends_on = None


# Verbatim live institution_locations expression: baseline
# (20260510_consolidated_baseline) + group read (20260619_group_loc_read).
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

# New: a location-scoped user reads every location they're assigned via
# user_locations. Read-only widening — WITH CHECK stays on the base
# expression, so writes still require the pinned/rebound app.location_id.
_LOCATIONS_ASSIGNED_READ = """
    OR (
        app_rls_context_type() = 'user'
        AND institution_locations.institution_id = app_rls_institution_id()
        AND app_rls_user_has_location(institution_locations.id)
    )
"""


def upgrade() -> None:
    op.create_table(
        "user_locations",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("institution_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["institution_id"], ["institutions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["institution_locations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_locations_user_id", "user_locations", ["user_id"])
    op.create_index(
        "ix_user_locations_institution_id", "user_locations", ["institution_id"]
    )
    op.create_index("ix_user_locations_location_id", "user_locations", ["location_id"])
    op.create_index(
        "ix_user_locations_user_location",
        "user_locations",
        ["user_id", "location_id"],
        unique=True,
    )

    op.execute("ALTER TABLE user_locations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_locations FORCE ROW LEVEL SECURITY")
    # ``institution_id`` is denormalized so this policy never joins ``users``
    # (whose own policy would recurse). Reads: the auth context loads the
    # requesting user's own rows; a user sees their own assignments; an
    # institution admin sees their institution's; a location admin sees rows
    # for their pinned location (mirrors the users-table policy). Writes:
    # institution admins within their institution, and super admins.
    op.execute(
        """
        CREATE POLICY user_locations_rls
        ON user_locations
        FOR ALL
        USING (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() = 'auth'
                AND user_locations.user_id = app_rls_user_id()
            )
            OR (
                app_rls_context_type() = 'user'
                AND user_locations.institution_id = app_rls_institution_id()
                AND (
                    user_locations.user_id = app_rls_user_id()
                    OR app_rls_role() = 'INSTITUTION_ADMIN'
                    OR (
                        app_rls_role() = 'LOCATION_ADMIN'
                        AND user_locations.location_id = app_rls_location_id()
                    )
                )
            )
        )
        WITH CHECK (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() = 'user'
                AND app_rls_role() = 'INSTITUTION_ADMIN'
                AND user_locations.institution_id = app_rls_institution_id()
            )
        )
        """
    )


    # Membership helper for the institution_locations read policy. plpgsql +
    # SECURITY DEFINER + BYPASSRLS owner, per the baseline's helper pattern
    # (SQL STABLE bodies get inlined into a recursive plan).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_user_has_location(loc uuid)
        RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE result boolean;
        BEGIN
            IF loc IS NULL OR app_rls_user_id() IS NULL THEN
                RETURN false;
            END IF;
            SELECT EXISTS (
                SELECT 1 FROM user_locations ul
                WHERE ul.user_id = app_rls_user_id()
                  AND ul.location_id = loc
            ) INTO result;
            RETURN result;
        END $$;
        """
    )
    op.execute(
        "ALTER FUNCTION app_rls_user_has_location(uuid) OWNER TO app_rls_definer"
    )
    op.execute("GRANT SELECT ON user_locations TO app_rls_definer")

    op.execute("DROP POLICY IF EXISTS institution_locations_rls ON institution_locations")
    op.execute(
        f"""
        CREATE POLICY institution_locations_rls ON institution_locations FOR ALL
        USING ({_LOCATIONS_BASE} {_LOCATIONS_GROUP_READ} {_LOCATIONS_ASSIGNED_READ})
        WITH CHECK ({_LOCATIONS_BASE})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS institution_locations_rls ON institution_locations")
    op.execute(
        f"""
        CREATE POLICY institution_locations_rls ON institution_locations FOR ALL
        USING ({_LOCATIONS_BASE} {_LOCATIONS_GROUP_READ})
        WITH CHECK ({_LOCATIONS_BASE})
        """
    )
    op.execute("DROP FUNCTION IF EXISTS app_rls_user_has_location(uuid)")
    op.execute("DROP POLICY IF EXISTS user_locations_rls ON user_locations")
    op.drop_index("ix_user_locations_user_location", table_name="user_locations")
    op.drop_index("ix_user_locations_location_id", table_name="user_locations")
    op.drop_index("ix_user_locations_institution_id", table_name="user_locations")
    op.drop_index("ix_user_locations_user_id", table_name="user_locations")
    op.drop_table("user_locations")
