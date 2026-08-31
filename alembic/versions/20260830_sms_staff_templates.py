"""Give staff SMS alerts editable templates, mirroring the staff email ones.

The SMS side previously had only patient-facing templates, and the three staff
alerts (call summary / urgent / appointment request) had their wording
hard-coded. This frees the bare ``appointment_request`` name for the staff
template — matching ``EmailTemplateType`` — by renaming the existing
patient acknowledgement to ``patient_appointment_request``, the same way email
prefixes ``patient_appointment_confirmation``.

No-PMS only in effect: the PMS patient confirmation (``appointment_booked``) is
untouched, and staff SMS alerts are only ever dispatched for no-PMS tenants.

Revision ID: 20260830_sms_staff_templates
Revises: 20260830_sms_recipient_location_scope
"""

from __future__ import annotations

from alembic import op


revision = "20260830_sms_staff_templates"
down_revision = "20260830_sms_recipient_location_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows hold the patient acknowledgement under the old name. Rename
    # before anything seeds a staff template under it, or the unique
    # (institution_id, template_type) key would collide.
    op.execute(
        """
        UPDATE sms_templates
        SET template_type = 'patient_appointment_request'
        WHERE template_type = 'appointment_request'
        """
    )


def downgrade() -> None:
    # Drop the staff templates first — they hold the name the patient row wants
    # back, and they have no meaning once the code is rolled back.
    op.execute(
        """
        DELETE FROM sms_templates
        WHERE template_type IN ('call_summary', 'urgent_alert', 'appointment_request')
        """
    )
    op.execute(
        """
        UPDATE sms_templates
        SET template_type = 'appointment_request'
        WHERE template_type = 'patient_appointment_request'
        """
    )
