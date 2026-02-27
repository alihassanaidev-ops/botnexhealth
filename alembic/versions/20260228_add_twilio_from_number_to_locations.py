"""add twilio_from_number to tenant_locations

Revision ID: 20260228_twilio_from_number
Revises: 20260226_add_jsonb_transcripts
Create Date: 2026-02-28
"""

from alembic import op
import sqlalchemy as sa

revision = "20260228_twilio_from_number"
down_revision = "20260226_add_jsonb_transcripts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_locations",
        sa.Column("twilio_from_number", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_locations", "twilio_from_number")
