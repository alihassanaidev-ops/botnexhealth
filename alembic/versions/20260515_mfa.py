"""Add MFA tables for WebAuthn, TOTP, and recovery codes.

Revision ID: 20260515_mfa
Revises: 20260514_audit_part
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260515_mfa"
down_revision = "20260514_audit_part"
branch_labels = None
depends_on = None


_MFA_TABLES = (
    "webauthn_credentials",
    "user_totp_factors",
    "mfa_recovery_codes",
)


def _mfa_policy_expr(table: str) -> str:
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() = 'user'
            AND {table}.user_id = app_rls_user_id()
        )
        OR (
            app_rls_context_type() IN ('auth', 'auth_mfa')
            AND {table}.user_id = app_rls_user_id()
        )
    """


def _table_exists(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    if not _table_exists("webauthn_credentials"):
        op.create_table(
            "webauthn_credentials",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=False),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=False),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("credential_id", sa.String(length=512), nullable=False),
            sa.Column("public_key", sa.Text(), nullable=False),
            sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("transports", sa.JSON(), nullable=True),
            sa.Column("device_label", sa.String(length=120), nullable=True),
            sa.Column("aaguid", sa.String(length=64), nullable=True),
            sa.Column("credential_device_type", sa.String(length=32), nullable=True),
            sa.Column(
                "credential_backed_up",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("credential_id", name="uq_webauthn_credential_id"),
        )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_webauthn_credentials_user_id
        ON webauthn_credentials (user_id)
        """
    )

    if not _table_exists("user_totp_factors"):
        op.create_table(
            "user_totp_factors",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=False),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=False),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("secret_encrypted", sa.Text(), nullable=False),
            sa.Column(
                "enabled_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("last_accepted_time_step", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_user_totp_factors_user_id
        ON user_totp_factors (user_id)
        """
    )

    if not _table_exists("mfa_recovery_codes"):
        op.create_table(
            "mfa_recovery_codes",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=False),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=False),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("code_hash", sa.String(length=255), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_mfa_recovery_codes_user_id
        ON mfa_recovery_codes (user_id)
        """
    )

    for table in _MFA_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_rls ON {table} FOR ALL
            USING ({_mfa_policy_expr(table)})
            WITH CHECK ({_mfa_policy_expr(table)})
            """
        )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexhealth_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                ON webauthn_credentials, user_totp_factors, mfa_recovery_codes
                TO nexhealth_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    for table in reversed(_MFA_TABLES):
        op.drop_table(table)
