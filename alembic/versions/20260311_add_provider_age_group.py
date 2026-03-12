"""Add min_age and max_age to institution_providers.

Revision ID: 20260311_provider_age
Revises: 20260310_notifications
Create Date: 2026-03-11

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260311_provider_age"
down_revision = "20260310_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "institution_providers",
        sa.Column(
            "min_age",
            sa.Integer(),
            nullable=True,
            comment="Minimum patient age (inclusive) this provider sees; NULL = no lower bound",
        ),
    )
    op.add_column(
        "institution_providers",
        sa.Column(
            "max_age",
            sa.Integer(),
            nullable=True,
            comment="Maximum patient age (inclusive) this provider sees; NULL = no upper bound",
        ),
    )


def downgrade() -> None:
    op.drop_column("institution_providers", "max_age")
    op.drop_column("institution_providers", "min_age")
