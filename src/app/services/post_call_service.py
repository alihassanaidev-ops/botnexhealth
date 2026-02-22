"""Service for processing post-call data from Retell webhooks."""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.call import Call, CallDirection, CallStatus, PatientStatus
from src.app.models.contact import Contact
from src.app.retell.models import RetellCallData, WebhookEvent

logger = logging.getLogger(__name__)


class PostCallService:
    """Handles business logic for saving post-call data securely."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _determine_patient_phone(self, call_data: RetellCallData) -> str | None:
        """Extract the most likely patient phone number based on direction."""
        # For inbound calls, patient is calling us (from_number).
        # For outbound calls, we are calling patient (to_number).
        if call_data.direction == "inbound":
            return call_data.from_number
        elif call_data.direction == "outbound":
            return call_data.to_number
        
        # Fallback: just return whichever exists if direction is unknown
        return call_data.from_number or call_data.to_number

    def _map_call_status(self, call_data: RetellCallData, analysis: dict[str, Any] | None) -> str:
        """Determine CallStatus from analysis data."""
        if not analysis:
            return CallStatus.NEEDS_FOLLOW_UP.value

        # Custom analysis from Retell
        custom_analysis = analysis.get("custom_analysis_data", {})
        
        # Example mapping based on what your Retell prompt might output
        if custom_analysis.get("appointment_booked") or analysis.get("call_successful"):
            return CallStatus.BOOKED.value
            
        if custom_analysis.get("is_emergency") or custom_analysis.get("patient_intent") == "emergency":
            return CallStatus.EMERGENCY.value
            
        if custom_analysis.get("requires_callback"):
            return CallStatus.NEEDS_FOLLOW_UP.value
        
        return CallStatus.NO_ACTION_NEEDED.value

    async def process_call_analyzed_event(
        self, tenant_id: str, webhook_call: RetellCallData, analysis: dict[str, Any] | None
    ) -> Call:
        """
        Process a call_analyzed event, upserting the Contact and creating a Call.
        
        Handles idempotency (retell_call_id is UNIQUE in DB).
        """
        # 1. Determine Identity
        phone = self._determine_patient_phone(webhook_call)
        
        contact: Contact | None = None
        
        if phone:
            # 2. Look up Contact by hash
            phone_hash = Contact.find_by_phone_hash(phone)
            existing_contact = (
                await self.session.execute(
                    select(Contact).where(
                        Contact.tenant_id == tenant_id,
                        Contact.phone_hash == phone_hash,
                    )
                )
            ).scalar_one_or_none()
            
            if existing_contact:
                contact = existing_contact
                # Update last interaction
                contact.last_agent_interaction_id = webhook_call.agent_id
            else:
                # Create new contact
                contact = Contact(
                    tenant_id=tenant_id,
                    is_new_patient=True,
                    last_agent_interaction_id=webhook_call.agent_id,
                )
                # Setting phone automatically handles AES encryption + hashing
                contact.phone = phone
                self.session.add(contact)
                await self.session.flush()

        # 3. Create Call record
        analysis_dict = analysis or {}
        
        # Calculate duration
        duration_ms = None
        if webhook_call.start_timestamp and webhook_call.end_timestamp:
            duration_ms = webhook_call.end_timestamp - webhook_call.start_timestamp

        call = Call(
            tenant_id=tenant_id,
            contact_id=contact.id if contact else None,
            retell_call_id=webhook_call.call_id,
            call_direction=webhook_call.direction,
            agent_used=webhook_call.agent_id,
            # Webhook model does not include transcript/recording_url directly 
            # for HIPAA compliance over log streams, but we capture what's sent.
            summary=analysis_dict.get("call_summary"),
            patient_sentiment=analysis_dict.get("user_sentiment"),
            call_status=self._map_call_status(webhook_call, analysis_dict),
            patient_status=PatientStatus.CONTACTED.value if webhook_call.direction == "outbound" else PatientStatus.NOT_CONTACTED.value,
            call_duration_seconds=(duration_ms // 1000) if duration_ms else None,
            is_new_patient=contact.is_new_patient if contact else True,
            is_complaint=analysis_dict.get("custom_analysis_data", {}).get("is_complaint", False),
            is_insurance_billing=analysis_dict.get("custom_analysis_data", {}).get("is_billing_question", False),
        )

        # Call Date/Time determination (fallback to now if missing)
        if webhook_call.start_timestamp:
            start_dt = datetime.fromtimestamp(webhook_call.start_timestamp / 1000, tz=timezone.utc)
            call.call_date = start_dt.date()
            call.call_time = start_dt.time()
        else:
            now = datetime.now(timezone.utc)
            call.call_date = now.date()
            call.call_time = now.time()

        self.session.add(call)
        
        # 4. Update contact counter
        if contact:
            # We must flush to get the call ID, but we won't count right now 
            # to keep it simple, call.times_called defaults to 1.
            pass

        logger.info(
            f"Saved Call {webhook_call.call_id} for tenant {tenant_id} "
            f"(contact={'found' if contact else 'unknown'})"
        )
        
        # Note: We rely on the caller (webhooks.py) to transaction.commit()
        return call
