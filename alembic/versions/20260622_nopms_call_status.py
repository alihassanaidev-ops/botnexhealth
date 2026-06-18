"""Widen ck_calls_call_status for the no-PMS call-status vocabulary.

No-PMS agents emit request-style statuses (the agent can't transact in a PMS):
needs_booking, needs_reschedule, needs_cancellation, insurance_and_billing.
("Needs call back" normalizes to the existing needs_callback so it still lands
in the Callback Queue; "Financial" reuses financial_inquiry.) This widens the
CHECK constraint so those values are accepted alongside the PMS set.

Revision ID: 20260622_nopms_call_status
Revises: 20260621_workflow_status
"""

from __future__ import annotations

from alembic import op


revision = "20260622_nopms_call_status"
down_revision = "20260621_workflow_status"
branch_labels = None
depends_on = None


_PMS_VALUES = (
    "appointment_booked",
    "appointment_rescheduled",
    "appointment_cancelled",
    "emergency",
    "complaint",
    "needs_callback",
    "faq_handled",
    "financial_inquiry",
    "transferred",
    "insurance_verified",
    "insurance_unverified",
    "no_action_needed",
)
_NO_PMS_ADDITIONS = (
    "needs_booking",
    "needs_reschedule",
    "needs_cancellation",
    "insurance_and_billing",
)


def _constraint_sql(values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return (
        "ALTER TABLE calls ADD CONSTRAINT ck_calls_call_status "
        f"CHECK (call_status IS NULL OR call_status IN ({quoted}))"
    )


def upgrade() -> None:
    op.execute("ALTER TABLE calls DROP CONSTRAINT IF EXISTS ck_calls_call_status")
    op.execute(_constraint_sql(_PMS_VALUES + _NO_PMS_ADDITIONS))


def downgrade() -> None:
    op.execute("ALTER TABLE calls DROP CONSTRAINT IF EXISTS ck_calls_call_status")
    op.execute(_constraint_sql(_PMS_VALUES))
