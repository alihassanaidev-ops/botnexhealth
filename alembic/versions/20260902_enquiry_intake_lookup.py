"""Allow an intake token to resolve only its own source row.

Revision ID: 20260902_enquiry_intake_lookup
Revises: 20260902_campaign_link_run_lookup
"""

from __future__ import annotations

from alembic import op


revision = "20260902_enquiry_intake_lookup"
down_revision = "20260902_campaign_link_run_lookup"
branch_labels = None
depends_on = None


TABLE = "enquiry_intake_sources"
POLICY = "enquiry_intake_sources_token_lookup"


def upgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    op.execute(
        f"""
        CREATE POLICY enquiry_intake_sources_token_lookup
        ON enquiry_intake_sources FOR SELECT
        USING (
            app_rls_context_type() = 'enquiry_intake_lookup'
            AND token_hash = app_rls_external_id()
        )
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
