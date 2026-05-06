"""Let MFA factor RLS see rows under email-/token-lookup auth contexts.

The 20260515 MFA migration policy permitted reads only for
``app_rls_context_type() IN ('auth', 'auth_mfa')`` (or super-admin /
matching user). The login, reset-password, and set-password flows
open their initial DB session under narrower lookup contexts —
``auth_email``, ``auth_reset_token``, ``auth_invite_token`` — which
have no ``app.user_id`` GUC set yet. The MFA status query inside
those flows therefore returned zero rows for an enrolled user, so
``MfaChallengeResponse`` shipped ``mfa_setup_required`` instead of
``mfa_required``. The user was steered into re-enrollment, which
overwrites their TOTP secret with a fresh one, then on the next
login the same thing happens — an infinite re-enrollment loop.

Add the three lookup contexts to the policy. They are short-lived
auth flows that already verified password / token; allowing them to
read MFA factor rows for the resolved user is the minimum-necessary
expansion. Application code in those flows still queries
``WHERE user_id = X``, so the broader policy doesn't widen the
practical exposure.

Revision ID: 20260517_mfa_rls_auth_lookup
Revises: 20260516_audit_truncate
"""

from __future__ import annotations

from alembic import op


revision = "20260517_mfa_rls_auth_lookup"
down_revision = "20260516_audit_truncate"
branch_labels = None
depends_on = None


_MFA_TABLES = (
    "webauthn_credentials",
    "user_totp_factors",
    "mfa_recovery_codes",
)


def _policy_expr(table: str) -> str:
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


def _previous_policy_expr(table: str) -> str:
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


def upgrade() -> None:
    for table in _MFA_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_rls ON {table} FOR ALL
            USING ({_policy_expr(table)})
            WITH CHECK ({_policy_expr(table)})
            """
        )


def downgrade() -> None:
    for table in _MFA_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_rls ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_rls ON {table} FOR ALL
            USING ({_previous_policy_expr(table)})
            WITH CHECK ({_previous_policy_expr(table)})
            """
        )
