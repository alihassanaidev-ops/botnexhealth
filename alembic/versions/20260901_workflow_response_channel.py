"""Allow workflow-authored campaign response events.

Item 11 adds a campaign step that books an appointment directly. That outcome
belongs in campaign history and metrics, but labeling it as ``booking_link``
would be false: no patient opened a link or chose a slot on the public page.
This widens the channel constraint with an explicit ``workflow`` value.

Revision ID: 20260901_workflow_response_channel
Revises: 20260901_enquiry_intake_src
"""

from __future__ import annotations

from alembic import op

revision = "20260901_workflow_response_channel"
down_revision = "20260901_enquiry_intake_src"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE campaign_response_events "
        "DROP CONSTRAINT IF EXISTS ck_campaign_response_events_channel"
    )
    op.execute(
        "ALTER TABLE campaign_response_events "
        "ADD CONSTRAINT ck_campaign_response_events_channel "
        "CHECK (channel IN ('sms', 'voice', 'email', 'booking_link', 'workflow', 'staff'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE campaign_response_events "
        "DROP CONSTRAINT IF EXISTS ck_campaign_response_events_channel"
    )
    op.execute(
        "ALTER TABLE campaign_response_events "
        "ADD CONSTRAINT ck_campaign_response_events_channel "
        "CHECK (channel IN ('sms', 'voice', 'email', 'booking_link', 'staff'))"
    )
