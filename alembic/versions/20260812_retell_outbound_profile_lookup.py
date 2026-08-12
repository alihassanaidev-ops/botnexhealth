"""Resolve outbound voice-profile agents in Retell function-call lookups.

Pre-appointment calls use an agent stored on ``outbound_voice_profiles`` while
the original ``retell_lookup`` RLS path only recognized agents stored directly
on ``institution_locations``.  Keep the lookup fail-closed, but allow the one
active location/institution/profile selected by either mapping style.

Revision ID: 20260812_retell_profile_lookup
Revises: 20260811_gotracker_appt_writebacks
"""

from __future__ import annotations

from alembic import op

revision = "20260812_retell_profile_lookup"
down_revision = "20260811_gotracker_appt_writebacks"
branch_labels = None
depends_on = None


_LOCATION_FOR_ROUTING_AGENT_SQL = """
    CREATE OR REPLACE FUNCTION app_rls_location_for_retell_routing_agent(agent text)
    RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
    DECLARE result uuid;
    BEGIN
        IF agent IS NULL OR agent = '' THEN
            RETURN NULL;
        END IF;

        SELECT il.id INTO result
        FROM institution_locations il
        WHERE il.retell_agent_id = agent
          AND il.is_active = true
        LIMIT 1;

        IF result IS NOT NULL THEN
            RETURN result;
        END IF;

        SELECT il.id INTO result
        FROM outbound_voice_profiles profile
        JOIN institution_locations il
          ON il.id = profile.location_id
         AND il.institution_id = profile.institution_id
        WHERE profile.retell_agent_id = agent
          AND profile.is_active = true
          AND il.is_active = true
        LIMIT 1;

        RETURN result;
    END $$;
"""


_INSTITUTION_FOR_ROUTING_AGENT_SQL = """
    CREATE OR REPLACE FUNCTION app_rls_inst_for_retell_routing_agent(agent text)
    RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
    DECLARE result uuid;
    BEGIN
        IF agent IS NULL OR agent = '' THEN
            RETURN NULL;
        END IF;

        SELECT il.institution_id INTO result
        FROM institution_locations il
        WHERE il.retell_agent_id = agent
          AND il.is_active = true
        LIMIT 1;

        IF result IS NOT NULL THEN
            RETURN result;
        END IF;

        SELECT il.institution_id INTO result
        FROM outbound_voice_profiles profile
        JOIN institution_locations il
          ON il.id = profile.location_id
         AND il.institution_id = profile.institution_id
        WHERE profile.retell_agent_id = agent
          AND profile.is_active = true
          AND il.is_active = true
        LIMIT 1;

        RETURN result;
    END $$;
"""


def _institutions_expr(*, outbound_profiles: bool) -> str:
    retell_helper = (
        "app_rls_inst_for_retell_routing_agent"
        if outbound_profiles
        else "app_rls_inst_for_retell_agent"
    )
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter')
            AND institutions.id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'audit'
            AND institutions.id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'middleware_lookup'
            AND institutions.slug = app_rls_external_id()
        )
        OR (
            app_rls_context_type() = 'retell_lookup'
            AND institutions.id = {retell_helper}(app_rls_external_id())
        )
        OR (
            app_rls_context_type() = 'twilio_lookup'
            AND institutions.id = app_rls_inst_for_twilio_number(app_rls_external_id())
        )
        OR (
            app_rls_context_type() = 'gotracker_lookup'
            AND institutions.id = app_rls_inst_for_location(app_rls_location_id())
        )
        OR (
            app_rls_context_type() = 'gotracker_webhooks'
            AND institutions.id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND institutions.id = app_rls_institution_id()
        )
    """


def _institution_locations_expr(*, outbound_profiles: bool) -> str:
    retell_predicate = (
        "institution_locations.id = "
        "app_rls_location_for_retell_routing_agent(app_rls_external_id())"
        if outbound_profiles
        else "institution_locations.retell_agent_id = app_rls_external_id()"
    )
    return f"""
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
            AND {retell_predicate}
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
            app_rls_context_type() = 'gotracker_lookup'
            AND institution_locations.id = app_rls_location_id()
        )
        OR (
            app_rls_context_type() = 'gotracker_webhooks'
            AND institution_locations.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR institution_locations.id = app_rls_location_id()
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


def _outbound_voice_profiles_expr() -> str:
    return """
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter')
            AND outbound_voice_profiles.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR outbound_voice_profiles.location_id = app_rls_location_id()
            )
        )
        OR (
            app_rls_context_type() = 'user'
            AND outbound_voice_profiles.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR outbound_voice_profiles.location_id = app_rls_location_id()
            )
        )
    """


def _apply_rls(table: str, using_expr: str, check_expr: str | None = None) -> None:
    check_expr = check_expr or using_expr
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
    op.execute(
        f"CREATE POLICY {table}_rls ON {table} FOR ALL "
        f"USING ({using_expr}) WITH CHECK ({check_expr})"
    )


def upgrade() -> None:
    op.execute(_LOCATION_FOR_ROUTING_AGENT_SQL)
    op.execute(_INSTITUTION_FOR_ROUTING_AGENT_SQL)
    op.execute(
        "ALTER FUNCTION app_rls_location_for_retell_routing_agent(text) "
        "OWNER TO app_rls_definer"
    )
    op.execute(
        "ALTER FUNCTION app_rls_inst_for_retell_routing_agent(text) "
        "OWNER TO app_rls_definer"
    )
    op.execute("GRANT SELECT ON outbound_voice_profiles TO app_rls_definer")

    institutions_expr = _institutions_expr(outbound_profiles=True)
    institutions_group_read = """
        OR (
            app_rls_context_type() = 'user'
            AND app_rls_role() = 'GROUP_ADMIN'
            AND institutions.group_id = app_rls_group_id()
        )
    """
    _apply_rls(
        "institutions",
        f"{institutions_expr} {institutions_group_read}",
        institutions_expr,
    )
    institution_locations_expr = _institution_locations_expr(outbound_profiles=True)
    institution_locations_group_read = """
        OR (
            app_rls_context_type() = 'user'
            AND app_rls_role() = 'GROUP_ADMIN'
            AND institution_locations.institution_id = app_rls_institution_id()
        )
    """
    _apply_rls(
        "institution_locations",
        f"{institution_locations_expr} {institution_locations_group_read}",
        institution_locations_expr,
    )
    _apply_rls(
        "outbound_voice_profiles",
        _outbound_voice_profiles_expr(),
    )
    op.execute(
        "DROP POLICY IF EXISTS outbound_voice_profiles_retell_lookup "
        "ON outbound_voice_profiles"
    )
    op.execute(
        """
        CREATE POLICY outbound_voice_profiles_retell_lookup
        ON outbound_voice_profiles FOR SELECT
        USING (
            app_rls_context_type() = 'retell_lookup'
            AND outbound_voice_profiles.retell_agent_id = app_rls_external_id()
            AND outbound_voice_profiles.is_active = true
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS outbound_voice_profiles_retell_lookup "
        "ON outbound_voice_profiles"
    )
    _apply_rls("institutions", _institutions_expr(outbound_profiles=False))
    _apply_rls(
        "institution_locations",
        _institution_locations_expr(outbound_profiles=False),
    )
    _apply_rls(
        "outbound_voice_profiles",
        _outbound_voice_profiles_expr(),
    )

    op.execute("DROP FUNCTION IF EXISTS app_rls_inst_for_retell_routing_agent(text)")
    op.execute(
        "DROP FUNCTION IF EXISTS app_rls_location_for_retell_routing_agent(text)"
    )
    op.execute("REVOKE SELECT ON outbound_voice_profiles FROM app_rls_definer")
