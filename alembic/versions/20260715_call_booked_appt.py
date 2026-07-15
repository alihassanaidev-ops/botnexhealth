"""Add booked appointment type to calls.

Captured post-call (best-effort) from the book_appointment invocation so the
calls/callbacks UI can show which appointment type a call booked. Additive,
nullable columns — safe to run before or after the app rollout.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260715_call_booked_appt"
down_revision = "20260701_sms_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent (IF NOT EXISTS): on a live prod DB the columns are pre-applied
    # before the code rollout to avoid a migrate-after-traffic-shift 500 window,
    # so this upgrade must reconcile cleanly whether or not they already exist.
    op.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS booked_appointment_type_id VARCHAR(100)")
    op.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS booked_appointment_type_name VARCHAR(255)")


def downgrade() -> None:
    op.execute("ALTER TABLE calls DROP COLUMN IF EXISTS booked_appointment_type_name")
    op.execute("ALTER TABLE calls DROP COLUMN IF EXISTS booked_appointment_type_id")
