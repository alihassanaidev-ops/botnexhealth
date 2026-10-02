"""Merge the campaign-qualification and form-integration migration branches.

Revision ID: 20260902_merge_qual_forms
Revises: 20260902_campaign_qual_metrics, 20260902_form_integrations
"""

from __future__ import annotations


revision = "20260902_merge_qual_forms"
down_revision = (
    "20260902_campaign_qual_metrics",
    "20260902_form_integrations",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two histories; both parent revisions perform the schema work."""


def downgrade() -> None:
    """Split back to the two parent heads without reverting either parent."""
