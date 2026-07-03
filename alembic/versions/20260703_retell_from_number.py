"""Add per-location Retell outbound caller-ID (Plan 03).

Adds a nullable retell_from_number column to institution_locations. This is the
E.164 number (imported into Retell) used as the caller ID for outbound campaign
voice calls. Distinct from twilio_from_number (SMS).

Revision ID: 20260703_retell_from_number
Revises: 20260703_provisioning
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260703_retell_from_number"
down_revision = "20260703_provisioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "institution_locations",
        sa.Column("retell_from_number", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("institution_locations", "retell_from_number")
