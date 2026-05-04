"""Narrow users RLS for auth flows: per-flow subcontexts + SECURITY DEFINER helpers.

Revision ID: 20260509_narrow_auth_users
Revises: 20260508_narrow_inst_lookup
Create Date: 2026-05-09

The original users policy in 20260506_rls_full_staged was tightened
to match the retell_lookup / twilio_lookup fix from 20260508. The
auth context still allowed broad reads when user_id was unset:

    OR (
        app_rls_context_type() = 'auth'
        AND (app_rls_user_id() IS NULL OR users.id = app_rls_user_id())
    )

That clause was added in 20260506 to unblock login (the original strict
form `users.id = app_rls_user_id()` returned 0 rows because login looks
up by email, not id). It worked, but RLS gave zero containment for any
query in `auth` context that didn't yet know the user_id — login,
forgot-password, reset-password, set-password — relying entirely on the
application-layer email/token-hash filter.

Fix: per-flow subcontexts + SECURITY DEFINER helpers, mirroring the
20260508 retell_lookup / twilio_lookup pattern.

  auth         — post-JWT path, narrow on users.id = app_rls_user_id()
  auth_email   — login + forgot-password, narrow via email helper
  auth_reset_token   — reset-password, narrow via reset-token helper
  auth_invite_token  — set-password, narrow via invite-token helper

Each helper is SECURITY DEFINER + STABLE, takes one column value
(already in the GUC the caller set) and returns the user.id for an
exact match. SECURITY DEFINER bypasses RLS for the helper body so the
policy itself never has to subquery the users table.

The helpers filter `deleted_at IS NULL` directly so soft-deleted users
are invisible regardless of caller.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260509_narrow_auth_users"
down_revision: Union[str, None] = "20260508_narrow_inst_lookup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_USERS_EXPR = """
    app_rls_is_super_admin()
    OR (
        app_rls_context_type() = 'auth'
        AND users.id = app_rls_user_id()
    )
    OR (
        app_rls_context_type() = 'auth_email'
        AND users.id = app_rls_user_for_email(app_rls_external_id())
    )
    OR (
        app_rls_context_type() = 'auth_reset_token'
        AND users.id = app_rls_user_for_reset_token(app_rls_external_id())
    )
    OR (
        app_rls_context_type() = 'auth_invite_token'
        AND users.id = app_rls_user_for_invite_token(app_rls_external_id())
    )
    OR (
        app_rls_context_type() = 'user'
        AND (
            users.id = app_rls_user_id()
            OR (
                users.institution_id = app_rls_institution_id()
                AND app_rls_role() = 'INSTITUTION_ADMIN'
            )
            OR (
                users.institution_id = app_rls_institution_id()
                AND users.location_id = app_rls_location_id()
                AND app_rls_role() = 'LOCATION_ADMIN'
            )
        )
    )
    OR (
        app_rls_context_type() IN ('celery', 'twilio', 'retell', 'dead_letter')
        AND users.institution_id = app_rls_institution_id()
    )
"""


_OLD_USERS_EXPR = """
    app_rls_is_super_admin()
    OR (
        app_rls_context_type() = 'auth'
        AND (app_rls_user_id() IS NULL OR users.id = app_rls_user_id())
    )
    OR (
        app_rls_context_type() = 'user'
        AND (
            users.id = app_rls_user_id()
            OR (
                users.institution_id = app_rls_institution_id()
                AND app_rls_role() = 'INSTITUTION_ADMIN'
            )
            OR (
                users.institution_id = app_rls_institution_id()
                AND users.location_id = app_rls_location_id()
                AND app_rls_role() = 'LOCATION_ADMIN'
            )
        )
    )
    OR (
        app_rls_context_type() IN ('celery', 'twilio', 'retell', 'dead_letter')
        AND users.institution_id = app_rls_institution_id()
    )
"""


def upgrade() -> None:
    # SECURITY DEFINER helpers. Each takes one column value (already known
    # to the caller via the GUC) and returns the id of the matching user.
    # deleted_at IS NULL filter applies regardless of caller context.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_user_for_email(addr text)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT id FROM users
            WHERE email = addr
              AND deleted_at IS NULL
              AND addr IS NOT NULL AND addr <> ''
            LIMIT 1
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_user_for_reset_token(h text)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT id FROM users
            WHERE password_reset_token_hash = h
              AND deleted_at IS NULL
              AND h IS NOT NULL AND h <> ''
            LIMIT 1
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_user_for_invite_token(h text)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT id FROM users
            WHERE invite_token_hash = h
              AND deleted_at IS NULL
              AND h IS NOT NULL AND h <> ''
            LIMIT 1
        $$;
        """
    )

    op.execute("DROP POLICY IF EXISTS users_rls ON users;")
    op.execute(
        f"""
        CREATE POLICY users_rls ON users
        FOR ALL
        USING ({_NEW_USERS_EXPR})
        WITH CHECK ({_NEW_USERS_EXPR});
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS users_rls ON users;")
    op.execute(
        f"""
        CREATE POLICY users_rls ON users
        FOR ALL
        USING ({_OLD_USERS_EXPR})
        WITH CHECK ({_OLD_USERS_EXPR});
        """
    )
    op.execute("DROP FUNCTION IF EXISTS app_rls_user_for_invite_token(text);")
    op.execute("DROP FUNCTION IF EXISTS app_rls_user_for_reset_token(text);")
    op.execute("DROP FUNCTION IF EXISTS app_rls_user_for_email(text);")
