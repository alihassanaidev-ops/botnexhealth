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
    op.add_column("calls", sa.Column("booked_appointment_type_id", sa.String(length=100), nullable=True))
    op.add_column("calls", sa.Column("booked_appointment_type_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "booked_appointment_type_name")
    op.drop_column("calls", "booked_appointment_type_id")
