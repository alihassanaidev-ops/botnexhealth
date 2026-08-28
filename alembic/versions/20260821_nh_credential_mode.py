"""Make the NexHealth credential mode an explicit choice.

Before this, "which key does this clinic use?" was inferred from whether
``institutions.nexhealth_api_key_encrypted`` happened to be populated, and a
missing or undecryptable key silently fell back to the platform key. A clinic
could therefore run on the shared ScaleNexus account without anyone choosing
that — consuming the platform's per-key rate limit, and (because NexHealth ties
webhook endpoint ownership to the authenticating key) risking orphaned webhook
subscriptions.

The mode is now stored and set deliberately by a super admin. Every existing row starts on 'platform', matching what the deployed code does
today, so this migration changes no runtime behaviour. That is NOT the same as
backfilling from whether a key is stored — the deployed adapter ignores stored
keys, so inferring from their presence would newly activate credentials that
have never been used.

Revision ID: 20260821_nh_credential_mode
Revises: 20260829_merge_campaign_email_retell_sms
"""

from __future__ import annotations

from alembic import op

revision = "20260821_nh_credential_mode"
down_revision = "20260829_merge_campaign_email_retell_sms"
branch_labels = None
depends_on = None

TABLE = "institutions"
COLUMN = "nexhealth_credential_mode"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {TABLE}
        ADD COLUMN IF NOT EXISTS {COLUMN} varchar(32) NOT NULL DEFAULT 'platform'
        """
    )
    # Deliberately NO backfill to 'institution'. Every existing row stays on
    # 'platform', which is exactly what both environments do today.
    #
    # It is tempting to infer "a stored key means they were already using it",
    # but that is false for the deployed code: the adapter reads
    # global_settings.nexhealth_api_key unconditionally, while the admin route
    # has been able to STORE a per-institution key for some time. Measured
    # 2026-08-21 — production: 1 institution, 0 with a stored key. Staging: 5
    # institutions, 1 with a stored key (acme-dental) that is inert because the
    # deployed image has no multi-account code.
    #
    # Backfilling that row to 'institution' would newly activate a credential
    # that has never been exercised, and because resolution now fails closed a
    # stale key would take that clinic offline rather than quietly falling back.
    # Enabling a clinic-owned key must be a deliberate, verified super-admin
    # action — which is the entire point of this column.
    op.execute(
        f"""
        ALTER TABLE {TABLE}
        ADD CONSTRAINT ck_{TABLE}_{COLUMN}
        CHECK ({COLUMN} IN ('platform', 'institution'))
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS ck_{TABLE}_{COLUMN}")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {COLUMN}")
