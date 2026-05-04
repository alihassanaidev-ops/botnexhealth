"""Replace users.email unique constraint with a partial index excluding soft-deleted rows.

Revision ID: 20260505_user_email_partial_unique
Revises: 20260505_audit_metadata_jsonb
Create Date: 2026-05-05

A full unique constraint on users.email blocks re-onboarding a user whose
email matches a previously soft-deleted account. We keep User rows forever
to preserve FK linkage to immutable audit_logs (HIPAA §164.530(j) — 6-year
retention of documentation about PHI handling), so re-using an email after
soft-delete must work.

The partial unique index enforces uniqueness only across active rows. Any
caller checking "does this email already exist?" must filter
``deleted_at IS NULL`` to match this guarantee.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260505_user_email_partial_unique"
down_revision: Union[str, None] = "20260505_audit_metadata_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The original constraint name depends on which alembic generation
    # produced it; drop both common variants defensively.
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_email")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS users_email_active_uq "
        "ON users (email) WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS users_email_active_uq")
    # Recreating the full unique constraint will fail if any duplicates
    # exist among soft-deleted rows; that's intentional — operators should
    # resolve duplicates manually before downgrading.
    op.execute("ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email)")
