"""Add Retell PII-scrubbed call variants to calls.

Stores Retell's already-redacted transcript / summary / recording URL
alongside the raw (encrypted) variants so the dashboard can show a non-PII
version by default while the raw stays behind the audited reveal endpoints.

Scrubbed values are non-PHI (Retell replaces PII with bracket placeholders),
so they are plaintext. Additive, nullable columns — safe to run before or
after the app rollout; idempotent (IF NOT EXISTS) for pre-apply on live prod.
"""

from alembic import op

revision = "20260720_call_scrubbed"
down_revision = "20260715_call_booked_appt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE calls ADD COLUMN IF NOT EXISTS "
        "scrubbed_transcript_with_tool_calls JSONB"
    )
    op.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS scrubbed_summary TEXT")
    op.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS scrubbed_recording_url TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE calls DROP COLUMN IF EXISTS scrubbed_recording_url")
    op.execute("ALTER TABLE calls DROP COLUMN IF EXISTS scrubbed_summary")
    op.execute(
        "ALTER TABLE calls DROP COLUMN IF EXISTS scrubbed_transcript_with_tool_calls"
    )
