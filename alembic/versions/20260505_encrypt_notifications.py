"""Encrypt in-app notification payload fields.

Revision ID: 20260505_encrypt_notifications
Revises: 20260505_audit_actor_check
Create Date: 2026-05-05

Notification title/message/data can include PHI because they are rendered to
clinic staff as call and callback context. Keep them readable to authorized
users through the application, but store them encrypted at rest.
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "20260505_encrypt_notifications"
down_revision: Union[str, None] = "20260505_audit_actor_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("title_encrypted", sa.Text(), nullable=True))
    op.add_column("notifications", sa.Column("message_encrypted", sa.Text(), nullable=True))
    op.add_column("notifications", sa.Column("data_encrypted", sa.Text(), nullable=True))

    bind = op.get_bind()
    _encrypt_existing_rows(bind)

    op.alter_column("notifications", "title_encrypted", nullable=False)
    op.alter_column("notifications", "message_encrypted", nullable=False)
    op.drop_column("notifications", "data")
    op.drop_column("notifications", "message")
    op.drop_column("notifications", "title")


def downgrade() -> None:
    op.add_column("notifications", sa.Column("title", sa.String(255), nullable=True))
    op.add_column("notifications", sa.Column("message", sa.Text(), nullable=True))
    op.add_column("notifications", sa.Column("data", JSONB(), nullable=True))

    bind = op.get_bind()
    _decrypt_existing_rows(bind)

    op.alter_column("notifications", "title", nullable=False)
    op.alter_column("notifications", "message", nullable=False)
    op.drop_column("notifications", "data_encrypted")
    op.drop_column("notifications", "message_encrypted")
    op.drop_column("notifications", "title_encrypted")


def _encrypt_existing_rows(bind: sa.engine.Connection) -> None:
    from src.app.models.institution import encrypt_value

    rows = bind.execute(
        sa.text("SELECT id, title, message, data FROM notifications")
    ).mappings()
    for row in rows:
        data_json = (
            json.dumps(row["data"], separators=(",", ":"), sort_keys=True)
            if row["data"] is not None
            else None
        )
        bind.execute(
            sa.text(
                """
                UPDATE notifications
                SET title_encrypted = :title_encrypted,
                    message_encrypted = :message_encrypted,
                    data_encrypted = :data_encrypted
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "title_encrypted": encrypt_value(row["title"] or ""),
                "message_encrypted": encrypt_value(row["message"] or ""),
                "data_encrypted": encrypt_value(data_json) if data_json is not None else None,
            },
        )


def _decrypt_existing_rows(bind: sa.engine.Connection) -> None:
    from src.app.models.institution import decrypt_value

    rows = bind.execute(
        sa.text(
            "SELECT id, title_encrypted, message_encrypted, data_encrypted "
            "FROM notifications"
        )
    ).mappings()
    for row in rows:
        data_raw = decrypt_value(row["data_encrypted"])
        bind.execute(
            sa.text(
                """
                UPDATE notifications
                SET title = :title,
                    message = :message,
                    data = CAST(:data AS JSONB)
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "title": decrypt_value(row["title_encrypted"]) or "",
                "message": decrypt_value(row["message_encrypted"]) or "",
                "data": data_raw,
            },
        )
