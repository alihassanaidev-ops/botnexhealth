"""Separate SES verification from live sending activation.

Revision ID: 20260902_email_identity_activation
Revises: 20260902_contact_rls
"""

from alembic import op


revision = "20260902_email_identity_activation"
down_revision = "20260902_contact_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guarded: on a fresh database the baseline's ``create_all`` has already
    # built this column from the model, so an unguarded ``add_column`` aborts
    # ``alembic upgrade head`` partway through the chain. That is what
    # ``tests/unit/test_migrations_are_replayable.py`` has been failing on.
    op.execute(
        "ALTER TABLE email_sending_identities "
        "ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE email_sending_identities DROP COLUMN IF EXISTS is_active"
    )
