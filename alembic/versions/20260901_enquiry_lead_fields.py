"""Give an enquiry the fields a lead actually needs.

Item 21 created the store with the minimum that unblocked it. Working leads
needs four more things, each answering a specific failure:

* ``email_hash`` — deduplicating on email alone breaks as soon as anything but
  a web form is involved, and on phone alone it breaks on recycled and
  reformatted numbers. Hashing both lets either match, and it is also the
  consent identity for the EMAIL channel, which ``consent_records`` already
  keys on.
* ``attribution`` — where the lead came from, structured. Attribution is
  notorious for vanishing at conversion, and once gone a clinic cannot tell
  which channel actually produces patients.
* ``external_ref`` — the submitting platform's own id, so a clinic can
  reconcile against the system the lead came from. Separate from
  ``intake_key``, which is ours and controls idempotency.
* ``notes_encrypted`` — free text for whoever works the lead. Encrypted with
  the rest, because a note about someone enquiring at a dental practice will
  say why they are enquiring: health information about a person who is not a
  patient and has consented to nothing.

Revision ID: 20260901_enquiry_lead_fields
Revises: 20260831_quiet_hours_exceptions
"""

from __future__ import annotations

from alembic import op

revision = "20260901_enquiry_lead_fields"
down_revision = "20260831_quiet_hours_exceptions"
branch_labels = None
depends_on = None

TABLE = "campaign_enquiries"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS email_hash varchar(64)"
    )
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS attribution jsonb")
    op.execute(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS external_ref varchar(160)"
    )
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS notes_encrypted text")
    # Matches the phone index beside it: a lead is looked up by whichever
    # identifier the channel happens to carry.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_campaign_enquiries_institution_email "
        f"ON {TABLE} (institution_id, email_hash)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_campaign_enquiries_institution_email")
    for column in ("notes_encrypted", "external_ref", "attribution", "email_hash"):
        op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {column}")
