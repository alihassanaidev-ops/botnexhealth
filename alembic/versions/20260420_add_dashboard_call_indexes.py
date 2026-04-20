"""Add dashboard-oriented indexes for calls.

Revision ID: 20260420_dashboard_call_indexes
Revises: 20260330_local_auth
Create Date: 2026-04-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260420_dashboard_call_indexes"
down_revision: Union[str, None] = "20260330_local_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Location-scoped dashboard counts and grouped metrics filter by institution, agent, and date.
    op.create_index(
        "ix_call_institution_agent_date",
        "calls",
        ["institution_id", "agent_used", "call_date"],
    )

    # Dashboard callback queue reads unresolved needs-callback items ordered by date/created_at.
    op.create_index(
        "ix_call_dashboard_open_callbacks",
        "calls",
        ["institution_id", "call_date", "created_at"],
        postgresql_where=sa.text(
            "call_status = 'needs_callback' AND callback_resolved = false"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_call_dashboard_open_callbacks", table_name="calls")
    op.drop_index("ix_call_institution_agent_date", table_name="calls")
