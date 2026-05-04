"""Lock audit_logs.actor to the AuditActor enum values via a CHECK constraint.

Revision ID: 20260505_audit_actor_check
Revises: 20260505_user_email_partial_unique
Create Date: 2026-05-05

Historically the application wrote inconsistent values into audit_logs.actor
(UUIDs, role strings, enum names). After the route-level cleanup that
normalized all call sites to AuditActor.* enum values, this constraint
prevents regressions: any future write that uses anything other than the
four enum values will fail at insert time.

The decorator function inside audit_logs_no_update / no_delete is dropped
and recreated to allow the ALTER TABLE through (since both triggers fire
BEFORE row-level changes, but the ALTER TABLE is DDL and uses a different
lock path — adding a CHECK constraint requires no row mutation).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260505_audit_actor_check"
down_revision: Union[str, None] = "20260505_user_email_partial_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_actor_check "
        "CHECK (actor IN ('RETELL_AGENT', 'ADMIN', 'SYSTEM', 'API_CLIENT'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS audit_logs_actor_check")
