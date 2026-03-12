"""Add transfer numbers per location.

Revision ID: 20260313_transfer_numbers
Revises: 20260313_invite_cooldown
Create Date: 2026-03-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260313_transfer_numbers"
down_revision: Union[str, None] = "20260313_invite_cooldown"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "institution_location_transfer_numbers",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column(
            "institution_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("institutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("institution_locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone_number", sa.String(length=50), nullable=False),
        sa.Column("department", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_transfer_numbers_institution_id",
        "institution_location_transfer_numbers",
        ["institution_id"],
    )
    op.create_index(
        "ix_transfer_numbers_location_id",
        "institution_location_transfer_numbers",
        ["location_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_transfer_numbers_location_id", table_name="institution_location_transfer_numbers")
    op.drop_index("ix_transfer_numbers_institution_id", table_name="institution_location_transfer_numbers")
    op.drop_table("institution_location_transfer_numbers")
