"""Convert audit_logs.audit_metadata from JSON to JSONB.

Revision ID: 20260505_audit_metadata_jsonb
Revises: 20260505_encrypt_call_transcript
Create Date: 2026-05-05

JSON cannot be GIN-indexed and forces a full JSON re-parse on every read.
Switching to JSONB enables efficient @> / ? containment queries on audit
metadata (commonly: filter by request_id, ip_address, actor_role).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260505_audit_metadata_jsonb"
down_revision: Union[str, None] = "20260505_encrypt_call_transcript"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE audit_logs "
        "ALTER COLUMN audit_metadata TYPE JSONB USING audit_metadata::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE audit_logs "
        "ALTER COLUMN audit_metadata TYPE JSON USING audit_metadata::json"
    )
