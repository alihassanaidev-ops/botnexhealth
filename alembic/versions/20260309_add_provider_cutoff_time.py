"""Add same_day_cutoff_time column to institution_providers.

Revision ID: 20260309_provider_cutoff
Revises: 20260309_provider_buffer
Create Date: 2026-03-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260309_provider_cutoff"
down_revision: Union[str, None] = "20260309_provider_buffer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "institution_providers",
        sa.Column(
            "same_day_cutoff_time",
            sa.Time(),
            nullable=True,
            comment="If set and current time > cutoff and provider has no appointments today, hide same-day slots",
        ),
    )


def downgrade() -> None:
    op.drop_column("institution_providers", "same_day_cutoff_time")
