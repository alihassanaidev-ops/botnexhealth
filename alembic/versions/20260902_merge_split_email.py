"""Merge the workflow split and multi-domain email migration branches.

Revision ID: 20260902_merge_split_email
Revises: 20260902_workflow_split_ab, 20260902_multi_domain_email
"""

from __future__ import annotations


revision = "20260902_merge_split_email"
down_revision = (
    "20260902_workflow_split_ab",
    "20260902_multi_domain_email",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
