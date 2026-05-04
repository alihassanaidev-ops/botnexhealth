"""
Twilio admin routes — phone number management and SMS sending.

All endpoints are admin-only and use the platform-level Twilio credentials
(TWILLIO_SID / TWILLIO_API_SECRET) configured in Render secrets.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.app.api.deps import get_current_admin
from src.app.api.rate_limit import RATE_READ, RATE_WRITE, limiter
from src.app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/twilio", tags=["Admin - Twilio"])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_twilio_client():
    """Initialise and return a Twilio REST client using platform credentials.
    Note: Prefer using SmsService over calling this directly to ensure SMS logging.
    """
    from twilio.rest import Client

    from src.app.config import settings

    account_sid = settings.twillio_sid
    auth_token = settings.twillio_api_secret

    if not account_sid or not auth_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twilio credentials not configured (TWILLIO_SID / TWILLIO_API_SECRET)",
        )

    return Client(account_sid, auth_token)


# ── Response models ───────────────────────────────────────────────────────────


class TwilioPhoneNumber(BaseModel):
    sid: str
    phone_number: str
    friendly_name: str
    capabilities: dict
    status: str | None = None


class SendSmsRequest(BaseModel):
    from_number: str = Field(..., description="Twilio phone number to send from (E.164)")
    to_number: str = Field(..., description="Recipient phone number (E.164)")
    body: str = Field(..., min_length=1, max_length=1600, description="SMS message body")
    institution_location_id: str = Field(..., description="Location UUID associated with this message")


class SendSmsResponse(BaseModel):
    message_sid: str
    status: str
    from_number: str
    to_number_masked: str | None


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/phone-numbers", response_model=list[TwilioPhoneNumber])
@limiter.limit(RATE_READ)
async def list_phone_numbers(
    request: Request,
    _: Annotated[User, Depends(get_current_admin)],
) -> list[TwilioPhoneNumber]:
    """
    List all Twilio phone numbers on the platform account.

    Returns number SID, E.164 phone number, friendly name, and capabilities
    (voice, SMS, MMS). Used by admins to see available numbers and configure
    tenants/locations.
    """
    try:
        client = _get_twilio_client()
        numbers = client.incoming_phone_numbers.list()
        return [
            TwilioPhoneNumber(
                sid=n.sid,
                phone_number=n.phone_number,
                friendly_name=n.friendly_name,
                capabilities={
                    "voice": n.capabilities.get("voice", False),
                    "sms": n.capabilities.get("sms", False),
                    "mms": n.capabilities.get("mms", False),
                },
                status="active",
            )
            for n in numbers
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list Twilio phone numbers: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to retrieve phone numbers from Twilio",
        )


@router.post("/send-sms", response_model=SendSmsResponse)
@limiter.limit(RATE_WRITE)
async def send_sms(
    request: Request,
    body: SendSmsRequest,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> SendSmsResponse:
    """
    Send an SMS via Twilio from one of the platform's phone numbers.

    The `from_number` must be an active Twilio number on the account.
    Both numbers must be in E.164 format (e.g. +12125551234).
    """
    from src.app.database import get_db_session
    from src.app.services.sms_service import SmsService
    from src.app.models.sms_history_log import SmsStatus
    from src.app.models.audit_log import AuditAction, AuditActor, AuditOutcome
    from src.app.services.audit import log_audit
    from src.app.services.sms_privacy import hash_for_logging

    try:
        async with get_db_session() as session:
            sms_service = SmsService(session)
            log_record = await sms_service.send_sms(
                from_number=body.from_number,
                to_number=body.to_number,
                body=body.body,
                institution_location_id=body.institution_location_id
            )

            # Commit the SMS history log regardless of Twilio success/failure
            await session.commit()

            if log_record.status == SmsStatus.FAILED.value:
                # If it's a configuration error throw 503, otherwise 502
                code = status.HTTP_503_SERVICE_UNAVAILABLE if "credentials" in str(log_record.error_message) else status.HTTP_502_BAD_GATEWAY
                raise HTTPException(
                    status_code=code,
                    detail=f"Failed to send SMS: {log_record.error_message}",
                )

            await log_audit(
                actor=AuditActor.ADMIN,
                action=AuditAction.SMS_SEND,
                target_resource=f"sms:{log_record.id}",
                outcome=AuditOutcome.SUCCESS,
                metadata={
                    "status": log_record.status,
                    "to_phone_hash": hash_for_logging(body.to_number),
                    "location_id": body.institution_location_id,
                },
                institution_id=(
                    str(log_record.institution_id)
                    if log_record.institution_id
                    else None
                ),
                user_id=str(current_admin.id),
                location_id=body.institution_location_id,
            )

            return SendSmsResponse(
                message_sid=log_record.message_sid or "",
                status=log_record.status,
                from_number=body.from_number,
                to_number_masked=log_record.to_number_masked,
            )
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning("Invalid SMS send request: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error("Failed to send SMS via Twilio: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send SMS",
        )
