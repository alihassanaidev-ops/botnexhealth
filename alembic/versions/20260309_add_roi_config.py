"""Add roi_config JSONB column to institutions table.

Revision ID: 20260309_roi_config
Revises: 20260309_invite_status
Create Date: 2026-03-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = "20260309_roi_config"
down_revision: Union[str, None] = "20260309_invite_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("institutions", sa.Column("roi_config", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("institutions", "roi_config")
