"""Add billing_email column to institutions table.

Revision ID: 20260312_billing_email
Revises: 20260310_notifications
Create Date: 2026-03-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260312_billing_email"
down_revision: Union[str, None] = "20260311_provider_age"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("institutions", sa.Column("billing_email", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("institutions", "billing_email")
