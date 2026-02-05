"""
Supabase service for handling backend operations like admin invites.
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
        
        Args:
            email: User's email address
            tenant_id: Optional tenant ID to store in user metadata
            role: Optional role to store in user metadata
        
        Returns:
            Supabase User object (or UserResponse)
        """
        if not self.client:
            raise RuntimeError("Supabase client is not initialized.")

        data = {}
        if tenant_id:
            data["tenant_id"] = tenant_id
        if role:
            data["role"] = role

        logger.info(f"Inviting user {email} to Supabase with data: {data}")
        
        try:
            # options argument expects a dictionary with a 'data' key for user metadata
            response = self.client.auth.admin.invite_user_by_email(
                email=email,
                options={"data": data}
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
            logger.critical(f"CRITICAL: Failed to delete Supabase user {user_id} during compensation: {e}")
            # In a real system, you'd send this to Sentry/PagerDuty immediately
            return False

