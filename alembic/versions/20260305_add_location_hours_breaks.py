"""Add location operating hours and breaks tables

Revision ID: 20260305_add_location_hours_breaks
Revises: 20260301_rename_tenant_to_institution
Create Date: 2026-03-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260305_add_location_hours_breaks"
down_revision = "20260301_rename_tenant_to_institution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- location_operating_hours --
    op.create_table(
        "location_operating_hours",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "location_id",
            UUID(as_uuid=False),
            sa.ForeignKey("institution_locations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("day_of_week", sa.Integer, nullable=False),
        sa.Column("is_open", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("open_time", sa.Time, nullable=True),
        sa.Column("close_time", sa.Time, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("location_id", "day_of_week", name="uq_location_day"),
    )

    # -- location_breaks --
    op.create_table(
        "location_breaks",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "location_id",
            UUID(as_uuid=False),
            sa.ForeignKey("institution_locations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("day_of_week", sa.Integer, nullable=True),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("location_breaks")
    op.drop_table("location_operating_hours")
