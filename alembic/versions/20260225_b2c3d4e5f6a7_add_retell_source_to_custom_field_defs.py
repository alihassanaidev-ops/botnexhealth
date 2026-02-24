"""add_retell_source_to_custom_field_defs

Adds retell_source and retell_source_key columns to custom_field_definitions
so tenants can map custom fields to specific keys in Retell webhook data.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-25 00:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "custom_field_definitions",
        sa.Column("retell_source", sa.String(30), nullable=True),
    )
    op.add_column(
        "custom_field_definitions",
        sa.Column("retell_source_key", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_field_definitions", "retell_source_key")
    op.drop_column("custom_field_definitions", "retell_source")
