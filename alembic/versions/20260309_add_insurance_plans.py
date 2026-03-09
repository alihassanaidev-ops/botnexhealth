"""Add insurance_plans table for location-scoped insurance list.

Revision ID: 20260309_insurance_plans
Revises: 20260309_roi_config
Create Date: 2026-03-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "20260309_insurance_plans"
down_revision: Union[str, None] = "20260309_roi_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "insurance_plans",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "institution_id",
            UUID(as_uuid=False),
            sa.ForeignKey("institutions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "location_id",
            UUID(as_uuid=False),
            sa.ForeignKey("institution_locations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("insurance_plans")
