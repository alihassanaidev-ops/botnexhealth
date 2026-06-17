"""Scalable group aggregation: let a GROUP_ADMIN read its whole group in one query.

The group dashboard previously looped one query-set per member institution
(N×queries). To stay flat as DSOs grow, the cross-group reads need to run as a
single aggregation. This replaces the per-institution-match GROUP_ADMIN clause
on call_metrics_daily with a group-membership clause (EXISTS over institutions
by group_id), so one ``GROUP BY institution_id`` over the rollup returns every
member — and only members — under the group context.

The EXISTS subquery is itself RLS-scoped (a GROUP_ADMIN can already read its
group's institutions via the institutions policy), so no SECURITY DEFINER is
needed and there's no recursion (the institutions policy never references
call_metrics_daily). It also still covers the single-institution drill-in
(institution_id is in the group → EXISTS true).

Revision ID: 20260620_group_agg_rls
Revises: 20260619_group_loc_read
"""

from __future__ import annotations

from alembic import op


revision = "20260620_group_agg_rls"
down_revision = "20260619_group_loc_read"
branch_labels = None
depends_on = None


def _call_metrics_policy(group_clause: str) -> str:
    return f"""
        CREATE POLICY call_metrics_daily_rls ON call_metrics_daily FOR ALL
        USING (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() IN ('retell', 'celery', 'twilio', 'dead_letter', 'audit')
                AND call_metrics_daily.institution_id = app_rls_institution_id()
            )
            OR (
                app_rls_context_type() = 'user'
                AND call_metrics_daily.institution_id = app_rls_institution_id()
                AND (
                    app_rls_location_id() IS NULL
                    OR call_metrics_daily.location_id = app_rls_location_id()
                    OR call_metrics_daily.location_id =
                        '00000000-0000-0000-0000-000000000000'::uuid
                )
            )
            {group_clause}
        )
        WITH CHECK (
            app_rls_is_super_admin()
            OR (
                app_rls_context_type() IN ('celery', 'audit')
                AND call_metrics_daily.institution_id = app_rls_institution_id()
            )
        )
    """


# New: a GROUP_ADMIN reads any member institution's rollup in one query.
_GROUP_MEMBERSHIP = """
            OR (
                app_rls_context_type() = 'user'
                AND app_rls_role() = 'GROUP_ADMIN'
                AND EXISTS (
                    SELECT 1 FROM institutions i
                    WHERE i.id = call_metrics_daily.institution_id
                      AND i.group_id = app_rls_group_id()
                )
            )
"""

# Old (from 20260618): single-institution match only.
_GROUP_SINGLE = """
            OR (
                app_rls_context_type() = 'user'
                AND app_rls_role() = 'GROUP_ADMIN'
                AND call_metrics_daily.institution_id = app_rls_institution_id()
            )
"""


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS call_metrics_daily_rls ON call_metrics_daily")
    op.execute(_call_metrics_policy(_GROUP_MEMBERSHIP))


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS call_metrics_daily_rls ON call_metrics_daily")
    op.execute(_call_metrics_policy(_GROUP_SINGLE))
