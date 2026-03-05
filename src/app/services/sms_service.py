"""
Service for sending SMS messages via Twilio and logging them for HIPAA compliance.

SOLID Principles Applied:
- SRP: Handles strictly the business logic of SMS sending and logging
- OCP: Easy to add new SMS providers in the future (could extract an interface)
- DIP: Depends on SQLAlchemy interfaces, not concrete DB instances
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from twilio.rest import Client

from src.app.config import settings
from src.app.models.sms_history_log import SmsHistoryLog, SmsStatus

logger = logging.getLogger(__name__)


class SmsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _get_twilio_client(self) -> Client:
        """Initialise and return a Twilio REST client using platform credentials."""
        account_sid = settings.twillio_sid
        auth_token = settings.twillio_api_secret

        if not account_sid or not auth_token:
            raise RuntimeError(
                "Twilio credentials not configured (TWILLIO_SID / TWILLIO_API_SECRET)"
            )

        return Client(account_sid, auth_token)

    async def send_sms(
        self,
        from_number: str,
        to_number: str,
        body: str,
        institution_location_id: str,
        patient_contact_id: str | None = None,
        call_id: str | None = None,
    ) -> SmsHistoryLog:
        """
        Send an SMS via Twilio and log the history in the database.
        
        Args:
            from_number: The Twilio phone number sending the message.
            to_number: The recipient's phone number.
            body: The SMS message content.
            institution_location_id: The location associated with this message.
            patient_contact_id: Optional ID of the Contact receiving this message.
            call_id: Optional associated Retell Call ID.
            
        Returns:
            The SmsHistoryLog database record.
        """
        # 1. Create pending log record
        sms_log = SmsHistoryLog(
            from_number=from_number,
            status=SmsStatus.PENDING.value,
            institution_location_id=institution_location_id,
            patient_contact_id=patient_contact_id,
            call_id=call_id,
        )
        
        # Set PHI fields using properties to trigger encryption
        sms_log.to_number = to_number
        sms_log.body = body
        
        self.session.add(sms_log)
        
        # Flush to get the ID and have it tracked in this transaction
        await self.session.flush()
        
        try:
            client = self._get_twilio_client()
            
            # Using asyncio to offload the blocking Twilio client network call
            import asyncio
            message = await asyncio.to_thread(
                client.messages.create,
                body=body,
                from_=from_number,
                to=to_number,
            )
            
            # Update log on success
            sms_log.status = SmsStatus.SENT.value
            sms_log.message_sid = message.sid
            logger.info("SMS sent successfully: sid=%s from=%s to=%s", message.sid, from_number, to_number)
            
        except RuntimeError as cred_err:
             # Configuration issue
            sms_log.status = SmsStatus.FAILED.value
            sms_log.error_message = str(cred_err)
            logger.error("Configuration error sending SMS: %s", cred_err)
        except Exception as e:
            # Update log on failure, being careful not to log full body/phone number here
            sms_log.status = SmsStatus.FAILED.value
            sms_log.error_message = str(e)
            logger.error("Failed to send SMS via Twilio. Error: %s", e)
            
        return sms_log

