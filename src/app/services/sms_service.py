"""
Service for sending SMS messages via Twilio and logging them for HIPAA compliance.

SOLID Principles Applied:
- SRP: Handles strictly the business logic of SMS sending and logging
- OCP: Easy to add new SMS providers in the future (could extract an interface)
- DIP: Depends on SQLAlchemy interfaces, not concrete DB instances
"""

import asyncio
import logging
from typing import Any
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from src.app.config import settings
from src.app.models.institution_location import InstitutionLocation
from src.app.models.sms_history_log import SmsHistoryLog, SmsStatus
from src.app.services.dead_letter import should_retry_vendor_error
from src.app.services.retention_policy import (
    default_sms_body_retain_until,
    default_sms_row_retain_until,
    retention_profile_for,
)
from src.app.services.sms_compliance import SmsComplianceService, SmsSendBlockedError
from src.app.services.sms_privacy import (
    hash_for_logging,
    prepare_outbound_sms_body,
    sanitize_provider_error,
)

logger = logging.getLogger(__name__)


class SmsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _get_twilio_client(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
    ) -> Client:
        """Return a Twilio REST client.

        Uses per-institution sub-account credentials when supplied; falls back
        to platform-level credentials from settings.
        """
        sid = account_sid or settings.twillio_sid
        token = auth_token or settings.twillio_api_secret

        if not sid or not token:
            raise RuntimeError(
                "Twilio credentials not configured (TWILLIO_SID / TWILLIO_API_SECRET)"
            )

        return Client(sid, token)

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
        location = (
            await self.session.execute(
                select(InstitutionLocation).where(
                    InstitutionLocation.id == institution_location_id
                )
            )
        ).scalar_one_or_none()
        if not location:
            raise ValueError("Institution location not found for SMS send")

        configured_from_number = (location.twilio_from_number or "").strip()
        requested_from_number = (from_number or "").strip()
        if not configured_from_number:
            raise ValueError("Location is missing a valid Twilio sender number")
        if requested_from_number != configured_from_number:
            raise ValueError(
                "SMS sender number does not match the location's configured Twilio number"
            )
        from_number = configured_from_number

        compliance = SmsComplianceService(self.session)
        identity = compliance.identify(to_number)
        from_hash = hash_for_logging(from_number)
        to_hash = hash_for_logging(to_number)
        now = datetime.now(timezone.utc)

        # Per-tenant retention: resolve the institution's SMS windows once and
        # reuse for both the suppressed-log and pending-log rows below.
        from src.app.models.institution import Institution

        institution = (
            await self.session.execute(
                select(Institution).where(Institution.id == location.institution_id)
            )
        ).scalar_one_or_none()
        _profile = retention_profile_for(institution) if institution else None
        _sms_body_days = _profile.sms_body_days if _profile else None
        _sms_meta_days = _profile.sms_metadata_days if _profile else None

        try:
            await compliance.assert_can_send(
                institution_id=location.institution_id,
                location_id=str(location.id),
                to_number=to_number,
                contact_id=patient_contact_id,
            )
            prepared_body = prepare_outbound_sms_body(
                body=body,
                clinic_identity=location.name,
            )
        except SmsSendBlockedError as blocked:
            # Use the structured reason code only — never the stringified
            # exception, which can incidentally contain free-text body bits
            # or PHI-shaped substrings the redactor wouldn't recognize.
            block_reason = blocked.reason
            sms_log = SmsHistoryLog(
                from_number=from_number,
                status=SmsStatus.SUPPRESSED.value,
                provider_status="suppressed",
                error_message=block_reason,
                institution_id=location.institution_id,
                location_id=str(location.id),
                institution_location_id=institution_location_id,
                patient_contact_id=patient_contact_id,
                call_id=call_id,
                to_number_hash=identity.phone_hash,
                to_number_masked=identity.phone_masked,
                last_status_at=now,
                retain_until=default_sms_row_retain_until(
                    now, metadata_days=_sms_meta_days, body_days=_sms_body_days
                ),
                body_retain_until=default_sms_body_retain_until(now, days=_sms_body_days),
            )
            sms_log.to_number = to_number
            sms_log.body = body
            self.session.add(sms_log)
            await self.session.flush()
            logger.info(
                "SMS suppressed: from_hash=%s to_hash=%s location_hash=%s reason=%s",
                from_hash,
                to_hash,
                hash_for_logging(str(location.id)),
                block_reason,
            )
            return sms_log

        # 1. Create pending log record
        sms_log = SmsHistoryLog(
            from_number=from_number,
            status=SmsStatus.PENDING.value,
            institution_id=location.institution_id,
            location_id=str(location.id),
            institution_location_id=institution_location_id,
            patient_contact_id=patient_contact_id,
            call_id=call_id,
            to_number_hash=identity.phone_hash,
            to_number_masked=identity.phone_masked,
            timestamp=now,
            retain_until=default_sms_row_retain_until(
                now, metadata_days=_sms_meta_days, body_days=_sms_body_days
            ),
            body_retain_until=default_sms_body_retain_until(now, days=_sms_body_days),
        )

        # Set PHI fields using properties to trigger encryption
        sms_log.to_number = to_number
        sms_log.body = prepared_body

        self.session.add(sms_log)

        # Flush to get the ID and have it tracked in this transaction
        await self.session.flush()

        try:
            client = self._get_twilio_client(
                account_sid=institution.twilio_account_sid if institution else None,
                auth_token=institution.twilio_auth_token if institution else None,
            )

            # Using asyncio to offload the blocking Twilio client network call
            create_kwargs: dict[str, Any] = {
                "body": prepared_body,
                "from_": from_number,
                "to": to_number,
            }
            if settings.twilio_sms_status_callback_url:
                create_kwargs["status_callback"] = (
                    settings.twilio_sms_status_callback_url
                )

            message = await asyncio.to_thread(
                client.messages.create,
                **create_kwargs,
            )

            # Update log on success
            sms_log.status = SmsStatus.SENT.value
            sms_log.message_sid = message.sid
            sms_log.provider_status = (
                getattr(message, "status", None) or SmsStatus.SENT.value
            )
            sms_log.last_status_at = datetime.now(timezone.utc)
            logger.info(
                "SMS sent successfully: sid_hash=%s from_hash=%s to_hash=%s location_hash=%s",
                hash_for_logging(message.sid),
                from_hash,
                to_hash,
                hash_for_logging(str(location.id)),
            )

        except RuntimeError as cred_err:
            # Configuration issue
            sms_log.status = SmsStatus.FAILED.value
            sms_log.provider_status = "config_error"
            sms_log.error_message = sanitize_provider_error(cred_err)
            sms_log.last_status_at = datetime.now(timezone.utc)
            logger.error(
                "Configuration error sending SMS: %s", sanitize_provider_error(cred_err)
            )
        except TwilioRestException as e:
            sms_log.status = SmsStatus.FAILED.value
            status_code = getattr(e, "status", None) or getattr(e, "code", None)
            retryable = should_retry_vendor_error(e)
            sms_log.provider_status = (
                f"{'retryable' if retryable else 'failed'}:{status_code or 'twilio'}"
            )
            sms_log.error_message = sanitize_provider_error(e)
            sms_log.last_status_at = datetime.now(timezone.utc)
            logger.error(
                "Failed to send SMS via Twilio: from_hash=%s to_hash=%s status=%s error=%s",
                from_hash,
                to_hash,
                status_code,
                sanitize_provider_error(e),
            )
        except Exception as e:
            # Update log on failure, being careful not to log full body/phone number here
            sms_log.status = SmsStatus.FAILED.value
            retryable = should_retry_vendor_error(e)
            sms_log.provider_status = (
                "retryable:network" if retryable else "failed:provider"
            )
            sms_log.error_message = sanitize_provider_error(e)
            sms_log.last_status_at = datetime.now(timezone.utc)
            logger.error(
                "Failed to send SMS via Twilio: from_hash=%s to_hash=%s error=%s",
                from_hash,
                to_hash,
                sanitize_provider_error(e),
            )

        return sms_log

    async def update_delivery_status(
        self,
        *,
        message_sid: str,
        provider_status: str,
        provider_error: str | None = None,
    ) -> SmsHistoryLog | None:
        """Update an SMS history row from a Twilio status callback."""
        row = (
            await self.session.execute(
                select(SmsHistoryLog).where(SmsHistoryLog.message_sid == message_sid)
            )
        ).scalar_one_or_none()
        if not row:
            return None

        status = provider_status.lower().strip()
        row.provider_status = status
        row.last_status_at = datetime.now(timezone.utc)
        if status == "delivered":
            row.status = SmsStatus.DELIVERED.value
        elif status in {"failed", "undelivered"}:
            row.status = SmsStatus.FAILED.value
            if provider_error:
                row.error_message = sanitize_provider_error(provider_error)
        elif status in {"sent"}:
            row.status = SmsStatus.SENT.value
        elif status in {"queued", "accepted", "scheduled", "sending"}:
            row.status = SmsStatus.PENDING.value
        return row
