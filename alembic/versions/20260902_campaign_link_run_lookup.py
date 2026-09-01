"""Allow a signed campaign link to resolve only its own workflow run.

Revision ID: 20260902_campaign_link_run_lookup
Revises: 20260901_merge_workflow_undeliv
"""

from __future__ import annotations

from alembic import op


revision = "20260902_campaign_link_run_lookup"
down_revision = "20260901_merge_workflow_undeliv"
branch_labels = None
depends_on = None


POLICY = "automation_workflow_runs_campaign_link_lookup"


def upgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON automation_workflow_runs")
    op.execute(
        f"""
        CREATE POLICY {POLICY}
        ON automation_workflow_runs FOR SELECT
        USING (
            app_rls_context_type() = 'campaign_link_lookup'
            AND automation_workflow_runs.id::text = app_rls_external_id()
        )
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON automation_workflow_runs")
