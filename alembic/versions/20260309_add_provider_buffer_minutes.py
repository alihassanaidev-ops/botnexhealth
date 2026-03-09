"""Add buffer_minutes column to institution_providers.

Revision ID: 20260309_provider_buffer
Revises: 20260309_insurance_plans
Create Date: 2026-03-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260309_provider_buffer"
down_revision: Union[str, None] = "20260309_insurance_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "institution_providers",
        sa.Column(
            "buffer_minutes",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Minimum booking lead-time in minutes for this provider",
        ),
    )


def downgrade() -> None:
    op.drop_column("institution_providers", "buffer_minutes")
