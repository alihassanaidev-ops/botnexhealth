"""Restore NexHealth webhook lookup RLS branches.

Revision ID: 20260908_nh_lookup_rls
Revises: 20260908_loc_campaign_rls
"""

from __future__ import annotations

from alembic import op


revision = "20260908_nh_lookup_rls"
down_revision = "20260908_loc_campaign_rls"
branch_labels = None
depends_on = None


_NEXHEALTH_MAPPING_HELPER_SQL = """
    CREATE OR REPLACE FUNCTION app_rls_inst_for_nexhealth_mapping(key text)
    RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
    DECLARE result uuid;
    BEGIN
        IF key IS NULL OR key = '' THEN
            RETURN NULL;
        END IF;

        WITH matches AS (
            SELECT DISTINCT institution_id
            FROM institution_locations
            WHERE key = ('subdomain:' || nexhealth_subdomain)
               OR key = ('location:' || nexhealth_location_id)
               OR key = (
                    'mapping:' || nexhealth_subdomain || ':' || nexhealth_location_id
               )
               OR (
                    key LIKE 'locations:%'
                    AND nexhealth_location_id = ANY(
                        string_to_array(substring(key FROM 11), ',')
                    )
               )
        ),
        counted AS (
            SELECT institution_id, count(*) OVER () AS match_count
            FROM matches
        )
        SELECT CASE WHEN match_count = 1 THEN institution_id ELSE NULL END
        INTO result
        FROM counted
        LIMIT 1;

        RETURN result;
    END $$;
"""


def _nexhealth_location_key_matches() -> str:
    return """
        (
            app_rls_external_id() = ('subdomain:' || institution_locations.nexhealth_subdomain)
            OR app_rls_external_id() = ('location:' || institution_locations.nexhealth_location_id)
            OR app_rls_external_id() = (
                'mapping:' || institution_locations.nexhealth_subdomain || ':'
                || institution_locations.nexhealth_location_id
            )
            OR (
                app_rls_external_id() LIKE 'locations:%'
                AND institution_locations.nexhealth_location_id = ANY(
                    string_to_array(substring(app_rls_external_id() FROM 11), ',')
                )
            )
        )
    """


def _institutions_expr(
    *,
    include_nexhealth: bool,
    include_group_read: bool,
) -> str:
    nexhealth = (
        """
        OR (
            app_rls_context_type() = 'nexhealth_lookup'
            AND institutions.id = app_rls_inst_for_nexhealth_mapping(app_rls_external_id())
        )
        OR (
            app_rls_context_type() = 'nexhealth_webhooks'
            AND institutions.id = app_rls_institution_id()
        )
        """
        if include_nexhealth
        else ""
    )
    group_read = (
        """
        OR (
            app_rls_context_type() = 'user'
            AND app_rls_role() = 'GROUP_ADMIN'
            AND institutions.group_id = app_rls_group_id()
        )
        """
        if include_group_read
        else ""
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
            AND institutions.id = app_rls_inst_for_retell_routing_agent(app_rls_external_id())
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
        {nexhealth}
        OR (
            app_rls_context_type() = 'user'
            AND institutions.id = app_rls_institution_id()
        )
        {group_read}
    """


def _institution_locations_expr(
    *,
    include_nexhealth: bool,
    include_group_read: bool,
) -> str:
    nexhealth = (
        f"""
        OR (
            app_rls_context_type() = 'nexhealth_lookup'
            AND institution_locations.institution_id =
                app_rls_inst_for_nexhealth_mapping(app_rls_external_id())
            AND {_nexhealth_location_key_matches()}
        )
        OR (
            app_rls_context_type() = 'nexhealth_webhooks'
            AND institution_locations.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR institution_locations.id = app_rls_location_id()
            )
        )
        """
        if include_nexhealth
        else ""
    )
    group_read = (
        """
        OR (
            app_rls_context_type() = 'user'
            AND app_rls_role() = 'GROUP_ADMIN'
            AND institution_locations.institution_id = app_rls_institution_id()
        )
        """
        if include_group_read
        else ""
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
            AND institution_locations.id =
                app_rls_location_for_retell_routing_agent(app_rls_external_id())
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
        {nexhealth}
        OR (
            app_rls_context_type() = 'user'
            AND institution_locations.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR institution_locations.id = app_rls_location_id()
            )
        )
        {group_read}
    """


def _contacts_expr(
    table: str,
    *,
    include_nexhealth_webhooks: bool,
    include_nexhealth_lookup: bool,
) -> str:
    contexts = ["'retell'", "'celery'", "'twilio'", "'dead_letter'"]
    contexts.extend(["'gotracker_webhooks'", "'gotracker_lookup'"])
    if include_nexhealth_webhooks:
        contexts.append("'nexhealth_webhooks'")
    scoped_contexts = ", ".join(contexts)
    nexhealth_lookup = (
        f"""
        OR (
            app_rls_context_type() = 'nexhealth_lookup'
            AND {table}.institution_id =
                app_rls_inst_for_nexhealth_mapping(app_rls_external_id())
        )
        """
        if include_nexhealth_lookup
        else ""
    )
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ({scoped_contexts})
            AND {table}.institution_id = app_rls_institution_id()
        )
        {nexhealth_lookup}
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR EXISTS (
                    SELECT 1 FROM contact_location_accesses cla
                    WHERE cla.contact_id = contacts.id
                      AND cla.location_id = app_rls_location_id()
                )
            )
        )
    """


def _contact_access_expr(
    table: str,
    *,
    include_nexhealth_webhooks: bool,
    include_nexhealth_lookup: bool,
) -> str:
    contexts = ["'retell'", "'celery'", "'twilio'", "'dead_letter'"]
    contexts.extend(["'gotracker_webhooks'", "'gotracker_lookup'"])
    if include_nexhealth_webhooks:
        contexts.append("'nexhealth_webhooks'")
    scoped_contexts = ", ".join(contexts)
    nexhealth_lookup = (
        f"""
        OR (
            app_rls_context_type() = 'nexhealth_lookup'
            AND {table}.institution_id =
                app_rls_inst_for_nexhealth_mapping(app_rls_external_id())
        )
        """
        if include_nexhealth_lookup
        else ""
    )
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ({scoped_contexts})
            AND {table}.institution_id = app_rls_institution_id()
        )
        {nexhealth_lookup}
        OR (
            app_rls_context_type() = 'user'
            AND {table}.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR {table}.location_id = app_rls_location_id()
            )
        )
    """


def _appointment_type_expr(*, include_nexhealth: bool) -> str:
    nexhealth_lookup = (
        """
        OR (
            app_rls_context_type() = 'nexhealth_lookup'
            AND institution_appointment_types.institution_id =
                app_rls_inst_for_nexhealth_mapping(app_rls_external_id())
        )
        OR (
            app_rls_context_type() = 'nexhealth_webhooks'
            AND institution_appointment_types.institution_id = app_rls_institution_id()
            AND (
                app_rls_location_id() IS NULL
                OR institution_appointment_types.location_id = app_rls_location_id()
            )
        )
        """
        if include_nexhealth
        else ""
    )
    return f"""
        app_rls_is_super_admin()
        OR app_rls_context_type() IN (
            'auth', 'audit', 'retell', 'retell_lookup', 'retell_function',
            'celery', 'twilio', 'twilio_lookup', 'twilio_status',
            'dead_letter', 'middleware_lookup'
        )
        {nexhealth_lookup}
        OR (
            app_rls_context_type() = 'user'
            AND institution_appointment_types.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR institution_appointment_types.location_id IS NULL
                OR institution_appointment_types.location_id = app_rls_location_id()
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
    op.execute(_NEXHEALTH_MAPPING_HELPER_SQL)
    op.execute(
        "ALTER FUNCTION app_rls_inst_for_nexhealth_mapping(text) "
        "OWNER TO app_rls_definer"
    )
    op.execute("GRANT SELECT ON institution_locations TO app_rls_definer")

    _apply_rls(
        "institutions",
        _institutions_expr(include_nexhealth=True, include_group_read=True),
        _institutions_expr(include_nexhealth=False, include_group_read=False),
    )
    _apply_rls(
        "institution_locations",
        _institution_locations_expr(include_nexhealth=True, include_group_read=True),
        _institution_locations_expr(include_nexhealth=False, include_group_read=False),
    )
    _apply_rls(
        "contacts",
        _contacts_expr(
            "contacts",
            include_nexhealth_webhooks=True,
            include_nexhealth_lookup=True,
        ),
        _contacts_expr(
            "contacts",
            include_nexhealth_webhooks=True,
            include_nexhealth_lookup=False,
        ),
    )
    _apply_rls(
        "contact_location_accesses",
        _contact_access_expr(
            "contact_location_accesses",
            include_nexhealth_webhooks=True,
            include_nexhealth_lookup=True,
        ),
        _contact_access_expr(
            "contact_location_accesses",
            include_nexhealth_webhooks=True,
            include_nexhealth_lookup=False,
        ),
    )
    _apply_rls(
        "institution_appointment_types",
        _appointment_type_expr(include_nexhealth=True),
        _appointment_type_expr(include_nexhealth=False),
    )


def downgrade() -> None:
    _apply_rls(
        "institution_appointment_types",
        _appointment_type_expr(include_nexhealth=False),
    )
    _apply_rls(
        "contact_location_accesses",
        _contact_access_expr(
            "contact_location_accesses",
            include_nexhealth_webhooks=False,
            include_nexhealth_lookup=False,
        ),
    )
    _apply_rls(
        "contacts",
        _contacts_expr(
            "contacts",
            include_nexhealth_webhooks=False,
            include_nexhealth_lookup=False,
        ),
    )
    _apply_rls(
        "institution_locations",
        _institution_locations_expr(include_nexhealth=False, include_group_read=True),
        _institution_locations_expr(include_nexhealth=False, include_group_read=False),
    )
    _apply_rls(
        "institutions",
        _institutions_expr(include_nexhealth=False, include_group_read=True),
        _institutions_expr(include_nexhealth=False, include_group_read=False),
    )
    op.execute("DROP FUNCTION IF EXISTS app_rls_inst_for_nexhealth_mapping(text)")
