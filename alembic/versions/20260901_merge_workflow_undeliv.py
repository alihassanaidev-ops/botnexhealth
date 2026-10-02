"""Merge the workflow-response and undeliverable migration branches.

Revision ID: 20260901_merge_workflow_undeliv
Revises: 20260901_undeliv_resolution, 20260901_workflow_response_channel
"""

from __future__ import annotations


revision = "20260901_merge_workflow_undeliv"
down_revision = (
    "20260901_undeliv_resolution",
    "20260901_workflow_response_channel",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two histories; both parent revisions perform the schema work."""


def downgrade() -> None:
    """Split back to the two parent heads without reverting either parent."""
