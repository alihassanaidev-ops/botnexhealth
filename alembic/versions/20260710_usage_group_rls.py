"""Let a GROUP_ADMIN read its whole group's usage rollup (Plan 11 DSO/group endpoint).

Recreates the usage_cost_rollups RLS policy with an added GROUP_ADMIN
group-membership branch (EXISTS over institutions by group_id), mirroring
`20260620_group_agg_rls` for call_metrics_daily — so one query returns every
member institution's usage for the group-level report. The EXISTS subquery is
itself RLS-scoped (a GROUP_ADMIN can already read its group's institutions), so
no SECURITY DEFINER and no recursion.

Revision ID: 20260710_usage_group_rls
Revises: 20260709_inbound_sms
"""

from __future__ import annotations

from alembic import op

revision = "20260710_usage_group_rls"
down_revision = "20260709_inbound_sms"
branch_labels = None
depends_on = None

TABLE = "usage_cost_rollups"


def _expr(group_clause: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN ('celery', 'dead_letter', 'usage_metering')
            AND {TABLE}.institution_id = app_rls_institution_id()
        )
        OR (
            app_rls_context_type() = 'user'
            AND {TABLE}.institution_id = app_rls_institution_id()
            AND (
                app_rls_role() = 'INSTITUTION_ADMIN'
                OR {TABLE}.location_id = app_rls_location_id()
            )
        )
        {group_clause}
    """


_GROUP_MEMBERSHIP = f"""
        OR (
            app_rls_context_type() = 'user'
            AND app_rls_role() = 'GROUP_ADMIN'
            AND EXISTS (
                SELECT 1 FROM institutions i
                WHERE i.id = {TABLE}.institution_id
                  AND i.group_id = app_rls_group_id()
            )
        )
"""


def _recreate_policy(expr: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_rls ON {TABLE}")
    op.execute(
        f"CREATE POLICY {TABLE}_rls ON {TABLE} FOR ALL USING ({expr}) WITH CHECK ({expr})"
    )


def upgrade() -> None:
    _recreate_policy(_expr(_GROUP_MEMBERSHIP))


def downgrade() -> None:
    _recreate_policy(_expr(""))
