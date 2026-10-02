"""Fix infinite-recursion in the institutions RLS policy (GoTracker branch).

``20260722_gotracker_adapter_location_config`` added a ``gotracker_lookup``
branch to the ``institutions`` policy that inlined
``EXISTS (SELECT 1 FROM institution_locations ...)``. Because the
``institution_locations`` policy already subqueries ``institutions`` (the
group/middleware read branches), this closed a reference cycle:
``institutions`` policy -> institution_locations -> ``institution_locations``
policy -> institutions -> ... Postgres rejects any query on ``institutions``
with "infinite recursion detected in policy for relation institutions" — at
plan time, so even super-admin reads 500.

The baseline already solved exactly this for the retell/twilio lookups with
``plpgsql STABLE SECURITY DEFINER`` helpers owned by the ``app_rls_definer``
(BYPASSRLS) role, which query ``institution_locations`` without re-triggering
its policy. This migration follows that pattern: it adds
``app_rls_inst_for_location(uuid)`` and recreates ``institutions_rls`` so the
``gotracker_lookup`` branch calls the helper instead of an inline subquery.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260728_fix_inst_rls_recursion"
down_revision = "20260723_patient_workflow_status_events"
branch_labels = None
depends_on = None


# SECURITY DEFINER helper — mirrors app_rls_inst_for_retell_agent/twilio_number
# (LANGUAGE plpgsql to defeat planner inlining; owned by the BYPASSRLS role so
# the SELECT on institution_locations does not re-enter its RLS policy).
_LOCATION_HELPER_SQL = """
    CREATE OR REPLACE FUNCTION app_rls_inst_for_location(loc uuid)
    RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
    DECLARE result uuid;
    BEGIN
        IF loc IS NULL THEN
            RETURN NULL;
        END IF;
        SELECT institution_id INTO result FROM institution_locations
        WHERE id = loc LIMIT 1;
        RETURN result;
    END $$;
"""

_LOCATION_HELPER_OWNER = (
    "ALTER FUNCTION app_rls_inst_for_location(uuid) OWNER TO app_rls_definer;"
)


def _institutions_expr(*, recursive_gotracker: bool) -> str:
    """The institutions USING/CHECK expression.

    ``recursive_gotracker=False`` (upgrade) routes the gotracker_lookup branch
    through the SECURITY DEFINER helper. ``True`` (downgrade) restores the
    20260722 inline-subquery form.
    """
    if recursive_gotracker:
        gotracker = """
        OR (
            app_rls_context_type() = 'gotracker_lookup'
            AND EXISTS (
                SELECT 1 FROM institution_locations il
                WHERE il.institution_id = institutions.id
                  AND il.id = app_rls_location_id()
            )
        )
        OR (
            app_rls_context_type() = 'gotracker_webhooks'
            AND institutions.id = app_rls_institution_id()
        )
        """
    else:
        gotracker = """
        OR (
            app_rls_context_type() = 'gotracker_lookup'
            AND institutions.id = app_rls_inst_for_location(app_rls_location_id())
        )
        OR (
            app_rls_context_type() = 'gotracker_webhooks'
            AND institutions.id = app_rls_institution_id()
        )
        """
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
            AND institutions.id = app_rls_inst_for_retell_agent(app_rls_external_id())
        )
        OR (
            app_rls_context_type() = 'twilio_lookup'
            AND institutions.id = app_rls_inst_for_twilio_number(app_rls_external_id())
        )
        {gotracker}
        OR (
            app_rls_context_type() = 'user'
            AND institutions.id = app_rls_institution_id()
        )
    """


def _apply_institutions_policy(expr: str) -> None:
    op.execute("ALTER TABLE institutions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE institutions FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS institutions_rls ON institutions")
    op.execute(
        f"CREATE POLICY institutions_rls ON institutions FOR ALL "
        f"USING ({expr}) WITH CHECK ({expr})"
    )


def upgrade() -> None:
    op.execute(_LOCATION_HELPER_SQL)
    op.execute(_LOCATION_HELPER_OWNER)
    _apply_institutions_policy(_institutions_expr(recursive_gotracker=False))


def downgrade() -> None:
    # Restore the 20260722 (recursive) policy form, then drop the helper.
    _apply_institutions_policy(_institutions_expr(recursive_gotracker=True))
    op.execute("DROP FUNCTION IF EXISTS app_rls_inst_for_location(uuid)")
