"""Make a lead a contact, rather than a second kind of person.

`campaign_enquiries` was created (Item 21) on the reasoning that a lead has no
practice-software record and therefore nowhere to live. That reasoning was
wrong: `contacts` has always held records with no `nexhealth_patient_id` —
anyone who rang and was not matched — and on staging there are 390 of them
against 959 patients. The store a lead needed already existed.

Two tables for one kind of person cost the vocabulary "contact", "patient",
"lead" and "enquiry" for two states, a conversion step between them, and a
worse match rate: an arriving lead was compared only against other enquiries
rather than against everybody the practice knows.

So the columns move onto contacts and the definition becomes one sentence: a
contact with a practice-software id is a patient, a contact without one is a
lead until they register.

`campaign_enquiries` is deliberately left in place, not dropped. Expand first,
contract later, once nothing reads it — the same reason production migrations
here never drop in the same release that stops writing.

Revision ID: 20260901_lead_on_contact
Revises: 20260901_enquiry_intake_src
"""

from __future__ import annotations

from alembic import op

revision = "20260901_lead_on_contact"
down_revision = "20260901_enquiry_intake_src"
branch_labels = None
depends_on = None

TABLE = "contacts"


def upgrade() -> None:
    # Where they came from. NULL for the contacts that predate this: somebody
    # created by an inbound call is not a lead, and back-filling a source we do
    # not know would invent history.
    # Matching on phone alone breaks on recycled and reformatted numbers;
    # matching on email alone breaks the moment a channel other than a web form
    # is involved. Both are hashed so either can find somebody.
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS email_hash varchar(64)")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_institution_email_hash "
        f"ON {TABLE} (institution_id, email_hash) WHERE email_hash IS NOT NULL"
    )
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS lead_source varchar(80)")
    # NULL means "not a lead" rather than a status of its own, so existing
    # contacts keep meaning exactly what they meant.
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS lead_status varchar(32)")
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS intake_key varchar(160)")
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS attribution jsonb")
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS external_ref varchar(160)")
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS notes_encrypted text")

    # Idempotent resubmission, carried over from the enquiry table. Partial, so
    # the overwhelming majority of contacts — which have no intake key — are not
    # forced into a uniqueness constraint that would collide on NULL semantics
    # in some engines and cost an index entry each in all of them.
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{TABLE}_institution_intake_key "
        f"ON {TABLE} (institution_id, intake_key) WHERE intake_key IS NOT NULL"
    )
    # The working list is "leads for this clinic, newest first".
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_institution_lead_status "
        f"ON {TABLE} (institution_id, lead_status) WHERE lead_status IS NOT NULL"
    )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS ix_{TABLE}_institution_lead_status")
    op.execute(f"DROP INDEX IF EXISTS uq_{TABLE}_institution_intake_key")
    op.execute(f"DROP INDEX IF EXISTS ix_{TABLE}_institution_email_hash")
    for column in (
        "notes_encrypted", "external_ref", "attribution",
        "intake_key", "lead_status", "lead_source", "email_hash",
    ):
        op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {column}")
