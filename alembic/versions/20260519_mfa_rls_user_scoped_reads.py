"""Bind MFA-table reads to ``app.user_id``.

The 20260518 migration narrowed write access on MFA tables but kept
the read policy open: any session running under ``auth``, ``auth_mfa``,
``auth_email``, ``auth_reset_token``, or ``auth_invite_token`` could
SELECT every row in ``webauthn_credentials``, ``user_totp_factors``,
and ``mfa_recovery_codes``. The intent at the time was "the
application layer already filters by user_id, so the broader policy
doesn't widen the practical exposure" — but a query bug or SQL
injection inside one of those contexts could quietly leak another
user's factor state.

This migration tightens reads to require ``mfa_table.user_id =
app_rls_user_id()`` in every context except super-admin. To keep the
existing flows working, the login / reset-password / set-password
endpoints have been refactored (auth.py: ``_load_mfa_status_for_user``)
to read MFA status in a separate session opened under the ``auth_mfa``
context with ``user_id`` set, after the user is resolved.

The lookup contexts (``auth_email``, ``auth_reset_token``,
``auth_invite_token``) lose all MFA-table read access — they only
need to find the user by their lookup key, which happens via the
``users`` table RLS, not the MFA tables.

Revision ID: 20260519_mfa_rls_user_reads
Revises: 20260518_mfa_rls_narrow
"""

from __future__ import annotations

from alembic import op


revision = "20260519_mfa_rls_user_reads"
down_revision = "20260518_mfa_rls_narrow"
branch_labels = None
depends_on = None


_MFA_TABLES = (
    "webauthn_credentials",
    "user_totp_factors",
    "mfa_recovery_codes",
)


def _read_expr_strict(table: str) -> str:
    """User-scoped read policy.

    Every context that reads MFA rows must have ``app.user_id`` set and
    matching the row owner. The auth-flow contexts that don't have a
    user_id (auth_email / auth_reset_token / auth_invite_token) no
    longer get any MFA read access — the auth routes were updated to
    open a separate ``auth_mfa`` session after the user is resolved.
    """
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() IN (
                'user', 'auth', 'auth_mfa'
            )
            AND {table}.user_id = app_rls_user_id()
        )
    """


def _read_expr_previous(table: str) -> str:
    """Restored on downgrade — matches the 20260518 read policy."""
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() = 'user'
            AND {table}.user_id = app_rls_user_id()
        )
        OR app_rls_context_type() IN (
            'auth', 'auth_mfa',
            'auth_email', 'auth_reset_token', 'auth_invite_token'
        )
    """


def upgrade() -> None:
    for table in _MFA_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls_read ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_rls_read ON {table} FOR SELECT
            USING ({_read_expr_strict(table)})
            """
        )


def downgrade() -> None:
    for table in _MFA_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls_read ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_rls_read ON {table} FOR SELECT
            USING ({_read_expr_previous(table)})
            """
        )
