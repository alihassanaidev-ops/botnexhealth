"""Harden SMS PHI handling and compliance constraints.

Revision ID: 20260501_sms_phi_hardening
Revises: 20260501_sms_compliance_dlq
Create Date: 2026-05-01
"""

from __future__ import annotations

import json
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260501_sms_phi_hardening"
down_revision: Union[str, None] = "20260501_sms_compliance_dlq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("dead_letter_events", sa.Column("redacted_payload_encrypted", sa.Text(), nullable=True))
    _backfill_encrypted_redacted_payload(bind)
    op.drop_column("dead_letter_events", "redacted_payload")

    _backfill_sms_history_phone_metadata(bind)
    _log_irrehashable_phone_hash_tables(bind)

    # TODO: consent_records, sms_suppressions, and do_not_contact do not retain the
    # original phone number by design. Their existing hashes remain auditable, but
    # rows created before E.164 canonicalization may not be found by new hash lookups.

    _deduplicate_active_suppressions(bind)
    _deduplicate_active_do_not_contact(bind)

    op.create_index(
        "uq_sms_suppressions_active_institution_channel_phone",
        "sms_suppressions",
        ["institution_id", "channel", "phone_hash"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index(
        "uq_do_not_contact_active_institution_phone",
        "do_not_contact",
        ["institution_id", "phone_hash"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    op.create_check_constraint("ck_consent_records_channel", "consent_records", "channel IN ('sms')")
    op.create_check_constraint(
        "ck_consent_records_status",
        "consent_records",
        "status IN ('granted', 'revoked')",
    )
    op.create_check_constraint(
        "ck_consent_records_source",
        "consent_records",
        "source IN ('manual', 'twilio_keyword', 'system')",
    )
    op.create_check_constraint("ck_sms_suppressions_channel", "sms_suppressions", "channel IN ('sms')")
    op.create_check_constraint(
        "ck_sms_suppressions_source",
        "sms_suppressions",
        "source IN ('manual', 'twilio_keyword', 'system')",
    )
    op.create_check_constraint(
        "ck_do_not_contact_source",
        "do_not_contact",
        "source IN ('manual', 'twilio_keyword', 'system')",
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_constraint("ck_do_not_contact_source", "do_not_contact", type_="check")
    op.drop_constraint("ck_sms_suppressions_source", "sms_suppressions", type_="check")
    op.drop_constraint("ck_sms_suppressions_channel", "sms_suppressions", type_="check")
    op.drop_constraint("ck_consent_records_source", "consent_records", type_="check")
    op.drop_constraint("ck_consent_records_status", "consent_records", type_="check")
    op.drop_constraint("ck_consent_records_channel", "consent_records", type_="check")

    op.drop_index("uq_do_not_contact_active_institution_phone", table_name="do_not_contact")
    op.drop_index("uq_sms_suppressions_active_institution_channel_phone", table_name="sms_suppressions")

    op.alter_column("sms_history_logs", "to_number_masked", nullable=True)
    op.alter_column("sms_history_logs", "to_number_hash", nullable=True)

    op.add_column("dead_letter_events", sa.Column("redacted_payload", sa.JSON(), nullable=True))
    _restore_json_redacted_payload(bind)
    op.drop_column("dead_letter_events", "redacted_payload_encrypted")


def _backfill_encrypted_redacted_payload(bind: sa.Connection) -> None:
    from src.app.models.institution import encrypt_value

    rows = bind.execute(
        sa.text("SELECT id, redacted_payload FROM dead_letter_events WHERE redacted_payload IS NOT NULL")
    ).mappings()
    for row in rows:
        text = json.dumps(row["redacted_payload"], sort_keys=True, default=str, separators=(",", ":"))
        bind.execute(
            sa.text(
                "UPDATE dead_letter_events "
                "SET redacted_payload_encrypted = :payload "
                "WHERE id = :id"
            ),
            {"id": row["id"], "payload": encrypt_value(text)},
        )


def _restore_json_redacted_payload(bind: sa.Connection) -> None:
    from src.app.models.institution import decrypt_value

    rows = bind.execute(
        sa.text(
            "SELECT id, redacted_payload_encrypted "
            "FROM dead_letter_events "
            "WHERE redacted_payload_encrypted IS NOT NULL"
        )
    ).mappings()
    for row in rows:
        try:
            text = decrypt_value(row["redacted_payload_encrypted"])
            payload = json.loads(text) if text else None
        except Exception as exc:
            logger.warning("Could not decrypt redacted_payload for DLQ row %s: %s", row["id"], exc)
            payload = None
        stmt = sa.text(
            "UPDATE dead_letter_events SET redacted_payload = :payload WHERE id = :id"
        ).bindparams(sa.bindparam("payload", type_=sa.JSON()))
        bind.execute(
            stmt,
            {"id": row["id"], "payload": payload},
        )


def _backfill_sms_history_phone_metadata(bind: sa.Connection) -> None:
    from src.app.models.institution import decrypt_value
    from src.app.services.sms_privacy import hash_phone, mask_phone

    unresolved = 0
    rows = bind.execute(
        sa.text(
            "SELECT id, to_number_encrypted "
            "FROM sms_history_logs "
            "WHERE to_number_encrypted IS NOT NULL"
        )
    ).mappings()
    for row in rows:
        try:
            phone = decrypt_value(row["to_number_encrypted"])
            phone_hash = hash_phone(phone)
            phone_masked = mask_phone(phone)
        except Exception as exc:
            unresolved += 1
            logger.warning("Could not decrypt SMS phone for history row %s: %s", row["id"], exc)
            continue

        if not phone_hash:
            unresolved += 1
            logger.warning("Could not normalize SMS phone for history row %s", row["id"])
            continue

        bind.execute(
            sa.text(
                "UPDATE sms_history_logs "
                "SET to_number_hash = :phone_hash, to_number_masked = :phone_masked "
                "WHERE id = :id"
            ),
            {"id": row["id"], "phone_hash": phone_hash, "phone_masked": phone_masked},
        )

    remaining_nulls = bind.execute(
        sa.text(
            "SELECT count(*) FROM sms_history_logs "
            "WHERE to_number_hash IS NULL OR to_number_masked IS NULL"
        )
    ).scalar()
    if unresolved == 0 and not remaining_nulls:
        op.alter_column("sms_history_logs", "to_number_hash", nullable=False)
        op.alter_column("sms_history_logs", "to_number_masked", nullable=False)
    else:
        logger.warning(
            "Leaving sms_history_logs phone metadata nullable because %s rows are null and %s rows could not be re-derived",
            remaining_nulls,
            unresolved,
        )


def _log_irrehashable_phone_hash_tables(bind: sa.Connection) -> None:
    for table_name in ("consent_records", "sms_suppressions", "do_not_contact"):
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table_name}")).scalar()
        if count:
            logger.warning(
                "TODO: %s has %s existing phone_hash rows without original phones; leaving hashes unchanged",
                table_name,
                count,
            )


def _deduplicate_active_suppressions(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY institution_id, channel, phone_hash
                        ORDER BY created_at ASC, id ASC
                    ) AS rn
                FROM sms_suppressions
                WHERE is_active = true
            )
            UPDATE sms_suppressions s
            SET
                is_active = false,
                released_at = now(),
                reason = CASE
                    WHEN s.reason IS NULL OR s.reason = '' THEN 'dedup at migration'
                    ELSE s.reason || '; dedup at migration'
                END
            FROM ranked
            WHERE s.id = ranked.id AND ranked.rn > 1
            """
        )
    )


def _deduplicate_active_do_not_contact(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY institution_id, phone_hash
                        ORDER BY created_at ASC, id ASC
                    ) AS rn
                FROM do_not_contact
                WHERE is_active = true
            )
            UPDATE do_not_contact d
            SET
                is_active = false,
                released_at = now(),
                reason = CASE
                    WHEN d.reason IS NULL OR d.reason = '' THEN 'dedup at migration'
                    ELSE d.reason || '; dedup at migration'
                END
            FROM ranked
            WHERE d.id = ranked.id AND ranked.rn > 1
            """
        )
    )
