"""Narrow MFA-table RLS for auth-lookup contexts: read-only.

The 20260517 migration broadened the MFA-table policy so the
``auth_email``, ``auth_reset_token``, and ``auth_invite_token`` contexts
could read factor rows during the password / invite flows that need to
detect "does this user already have MFA enrolled?" before the wider
``auth_mfa`` context is in play. That fix was correct, but it widened
the policy to ``FOR ALL`` — meaning a query running under one of those
narrow auth contexts could not only SELECT but also INSERT, UPDATE, or
DELETE MFA rows.

In practice the password flows only ever read MFA state; mutating
operations (enrol, disable, regenerate recovery codes, remove passkey)
always run under ``auth_mfa`` or the user's own ``user`` context. So the
broader contexts had no business with INSERT/UPDATE/DELETE, and a future
bug or misconfigured query under one of them could quietly mutate
another user's factor table without app-layer awareness.

This migration replaces the single ``FOR ALL`` policy on each MFA table
with a pair:

  * ``<table>_rls_read``: ``FOR SELECT`` — admits the lookup contexts
    so the password flows keep working.
  * ``<table>_rls_write``: ``FOR INSERT, UPDATE, DELETE`` — drops the
    lookup contexts; only ``user`` (matching user_id), ``auth``,
    ``auth_mfa``, and super-admin may write.

Revision ID: 20260518_mfa_rls_narrow
Revises: 20260517_mfa_rls_auth_lookup
"""

from __future__ import annotations

from alembic import op


revision = "20260518_mfa_rls_narrow"
down_revision = "20260517_mfa_rls_auth_lookup"
branch_labels = None
depends_on = None


_MFA_TABLES = (
    "webauthn_credentials",
    "user_totp_factors",
    "mfa_recovery_codes",
)


def _read_expr(table: str) -> str:
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


def _write_expr(table: str) -> str:
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


def _previous_policy_expr(table: str) -> str:
    """The 20260517 expression — restored on downgrade."""
    return f"""
        app_rls_is_super_admin()
        OR (
            app_rls_context_type() = 'user'
            AND {table}.user_id = app_rls_user_id()
        )
        OR (
            app_rls_context_type() IN (
                'auth', 'auth_mfa',
                'auth_email', 'auth_reset_token', 'auth_invite_token'
            )
        )
    """


def upgrade() -> None:
    for table in _MFA_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
        # SELECT: broader (lookup contexts allowed) — same shape as
        # 20260517 but only FOR SELECT instead of FOR ALL.
        op.execute(
            f"""
            CREATE POLICY {table}_rls_read ON {table} FOR SELECT
            USING ({_read_expr(table)})
            """
        )
        # INSERT / UPDATE / DELETE: write-side policies are split per
        # command because Postgres requires separate USING (for the row
        # being mutated) and WITH CHECK (for the row that would result)
        # semantics per command. Each only admits user-bound contexts.
        op.execute(
            f"""
            CREATE POLICY {table}_rls_insert ON {table} FOR INSERT
            WITH CHECK ({_write_expr(table)})
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_rls_update ON {table} FOR UPDATE
            USING ({_write_expr(table)})
            WITH CHECK ({_write_expr(table)})
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_rls_delete ON {table} FOR DELETE
            USING ({_write_expr(table)})
            """
        )


def downgrade() -> None:
    for table in _MFA_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls_read ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_rls_insert ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_rls_update ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_rls_delete ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_rls ON {table} FOR ALL
            USING ({_previous_policy_expr(table)})
            WITH CHECK ({_previous_policy_expr(table)})
            """
        )
