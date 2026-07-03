"""Add per-institution Twilio sub-account and email from-address fields (Plan 10).

Adds nullable encrypted credential columns to the institutions table for
per-institution Twilio sub-accounts and email sending identity.

Revision ID: 20260703_institution_provisioning
Revises: 20260703_consent_channel
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260703_provisioning"
down_revision = "20260703_consent_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("institutions", sa.Column("twilio_account_sid_encrypted", sa.Text(), nullable=True))
    op.add_column("institutions", sa.Column("twilio_auth_token_encrypted", sa.Text(), nullable=True))
    op.add_column("institutions", sa.Column("email_from_address", sa.String(320), nullable=True))
    op.add_column("institutions", sa.Column("email_from_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("institutions", "email_from_name")
    op.drop_column("institutions", "email_from_address")
    op.drop_column("institutions", "twilio_auth_token_encrypted")
    op.drop_column("institutions", "twilio_account_sid_encrypted")
