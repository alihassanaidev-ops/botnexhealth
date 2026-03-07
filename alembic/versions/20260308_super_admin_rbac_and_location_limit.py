"""Add institution location_limit and migrate roles to 4-tier RBAC.

Revision ID: 20260308_super_admin_rbac
Revises: c9e76b8323c7
Create Date: 2026-03-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260308_super_admin_rbac"
down_revision: Union[str, None] = "c9e76b8323c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pricing/plan control: super admin can cap institution location count.
    op.add_column(
        "institutions",
        sa.Column("location_limit", sa.Integer(), nullable=False, server_default="1"),
    )

    # Normalize old role values to the new 4-tier RBAC roles.
    op.execute("UPDATE users SET role = 'SUPER_ADMIN' WHERE role = 'ADMIN'")
    op.execute("UPDATE users SET role = 'INSTITUTION_ADMIN' WHERE role = 'INSTITUTION'")
    op.execute("UPDATE users SET role = 'LOCATION_ADMIN' WHERE role = 'LOCATION'")


def downgrade() -> None:
    # Revert role names back to legacy values.
    op.execute("UPDATE users SET role = 'ADMIN' WHERE role = 'SUPER_ADMIN'")
    op.execute("UPDATE users SET role = 'INSTITUTION' WHERE role = 'INSTITUTION_ADMIN'")
    op.execute("UPDATE users SET role = 'LOCATION' WHERE role = 'LOCATION_ADMIN'")

    op.drop_column("institutions", "location_limit")
