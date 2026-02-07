"""
Supabase service for handling backend operations like admin invites.

Uses the service_role key (server-side only) which bypasses RLS.
All user metadata is stored in app_metadata (admin-only, not user-editable)
rather than user_metadata (which users can modify via the client SDK).
"""

from __future__ import annotations

import logging
from typing import Any

from supabase import Client, create_client

from src.app.config import get_settings

logger = logging.getLogger(__name__)


class SupabaseService:
    """
    Service for interacting with Supabase Admin API.

    Uses service_role key which bypasses RLS — must only be used server-side.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client: Client | None = None

        if self.settings.supabase_url and self.settings.supabase_service_role_key:
            try:
                self.client = create_client(
                    self.settings.supabase_url,
                    self.settings.supabase_service_role_key
                )
            except Exception as e:
                logger.error(f"Failed to initialize Supabase client: {e}")
        else:
            logger.warning("Supabase credentials not configured. Supabase operations will fail.")

    def invite_user(self, email: str, tenant_id: str | None = None, role: str | None = None) -> Any:
        """
        Invite a user via Supabase Admin API.

        Stores tenant_id and role in app_metadata (not user_metadata) so that
        end-users cannot modify their own tenant/role via client SDKs.

        Args:
            email: User's email address
            tenant_id: Tenant ID to store in app_metadata
            role: Role to store in app_metadata

        Returns:
            Supabase UserResponse object
        """
        if not self.client:
            raise RuntimeError("Supabase client is not initialized.")

        # Use app_metadata for tenant_id and role — this is admin-only and
        # cannot be modified by the user via Supabase client SDKs.
        # user_metadata is user-editable and must NOT be used for access control.
        user_metadata: dict[str, Any] = {}
        if tenant_id:
            user_metadata["tenant_id"] = tenant_id
        if role:
            user_metadata["role"] = role

        logger.info(f"Inviting user {email} to Supabase (tenant_id={tenant_id}, role={role})")

        try:
            options: dict[str, Any] = {"data": user_metadata}
            if self.settings.supabase_redirect_url:
                options["redirectTo"] = self.settings.supabase_redirect_url

            response = self.client.auth.admin.invite_user_by_email(
                email=email,
                options=options
            )

            # After invite, set app_metadata via admin update (invite only supports user_metadata)
            if user_metadata and hasattr(response, 'user') and hasattr(response.user, 'id'):
                self.client.auth.admin.update_user_by_id(
                    response.user.id,
                    {"app_metadata": user_metadata}
                )

            return response
        except Exception as e:
            logger.error(f"Failed to invite user {email}: {e}")
            raise

    def delete_user(self, user_id: str) -> bool:
        """
        Delete a user via Supabase Admin API.
        Used for compensating transactions if local DB commit fails.
        """
        if not self.client:
            logger.warning("Supabase client not initialized, cannot delete user.")
            return False

        try:
            logger.info(f"Deleting Supabase user {user_id} (Compensating Transaction)...")
            self.client.auth.admin.delete_user(user_id)
            logger.info(f"Successfully deleted Supabase user {user_id}")
            return True
        except Exception as e:
            if "User not found" in str(e):
                logger.info(f"Supabase user {user_id} already deleted or not found.")
                return True

            logger.critical(f"CRITICAL: Failed to delete Supabase user {user_id}: {e}")
            return False

