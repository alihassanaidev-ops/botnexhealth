"""Service for processing post-call data from Retell webhooks."""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.call import Call, CallStatus, PatientStatus
from src.app.models.contact import Contact
from src.app.retell.models import RetellCallData

logger = logging.getLogger(__name__)


def _nonempty(value: str | None) -> str | None:
    """Return None for falsy or placeholder strings like 'None', 'N/A', 'n/a'."""
    if not value:
        return None
    stripped = value.strip()
    if stripped.lower() in ("none", "n/a", ""):
        return None
    return stripped


class PostCallService:
    """Handles business logic for saving post-call data securely."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _determine_patient_phone(self, call_data: RetellCallData) -> str | None:
        """Extract the most likely patient phone number based on call direction."""
        if call_data.direction == "inbound":
            return call_data.from_number
        if call_data.direction == "outbound":
            return call_data.to_number
        return call_data.from_number or call_data.to_number

    def _map_call_status(self, call_data: RetellCallData, analysis: dict[str, Any] | None) -> str:
        """Determine CallStatus from Retell analysis data.

        Key mapping (Title Case as configured in the Retell dashboard):
        - call_successful (top-level) → BOOKED
        - Complaining Patient         → NEEDS_FOLLOW_UP
        - Appointment Detail present  → BOOKED
        - fallback                    → NO_ACTION_NEEDED
        """
        if not analysis:
            return CallStatus.NEEDS_FOLLOW_UP.value

        if analysis.get("call_successful"):
            return CallStatus.BOOKED.value

        custom = analysis.get("custom_analysis_data", {})

        if custom.get("Complaining Patient"):
            return CallStatus.NEEDS_FOLLOW_UP.value

        appointment_detail = (custom.get("Appointment Detail") or "").strip().lower()
        if appointment_detail and appointment_detail not in ("none", "n/a", "no appointment"):
            return CallStatus.BOOKED.value

        return CallStatus.NO_ACTION_NEEDED.value

    @staticmethod
    def _parse_patient_name(raw: str | None) -> tuple[str | None, str | None, str | None]:
        """Split 'First Last' string into (first_name, last_name, full_name)."""
        if not raw or not raw.strip():
            return None, None, None
        parts = raw.strip().split(None, 1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else None
        return first, last, raw.strip()

    async def process_call_analyzed_event(
        self,
        tenant_id: str,
        webhook_call: RetellCallData,
        analysis: dict[str, Any] | None,
    ) -> Call:
        """
        Process a call_analyzed event: upsert Contact and create Call.

        Idempotency: retell_call_id is UNIQUE — duplicate webhooks are safe.
        """
        analysis_dict = analysis or {}
        custom = analysis_dict.get("custom_analysis_data", {})

        # ── 1. Resolve / create Contact ──────────────────────────────────────
        phone = self._determine_patient_phone(webhook_call)
        contact: Contact | None = None

        # Extract identity fields from custom analysis (Title Case keys)
        patient_name_str: str | None = custom.get("Patient name")
        patient_email: str | None = custom.get("Patient email")
        patient_dob: str | None = custom.get("Date of birth")
        is_new_patient_flag: bool = bool(custom.get("New_patient", False))
        first_name, last_name, full_name = self._parse_patient_name(patient_name_str)

        if phone:
            phone_hash = Contact.find_by_phone_hash(phone)
            existing = (
                await self.session.execute(
                    select(Contact).where(
                        Contact.tenant_id == tenant_id,
                        Contact.phone_hash == phone_hash,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                contact = existing
                contact.last_agent_interaction_id = webhook_call.agent_id
                # Enrich: only fill in blanks — never overwrite existing data
                if first_name and not contact.first_name:
                    contact.first_name = first_name
                    contact.last_name = last_name
                    contact.full_name = full_name
                if patient_email and not contact.email_encrypted:
                    contact.email = patient_email
                if patient_dob and not contact.date_of_birth_encrypted:
                    contact.date_of_birth = patient_dob
            else:
                contact = Contact(
                    tenant_id=tenant_id,
                    first_name=first_name,
                    last_name=last_name,
                    full_name=full_name,
                    is_new_patient=is_new_patient_flag,
                    last_agent_interaction_id=webhook_call.agent_id,
                )
                contact.phone = phone
                if patient_email:
                    contact.email = patient_email
                if patient_dob:
                    contact.date_of_birth = patient_dob
                self.session.add(contact)
                await self.session.flush()

        # ── 2. Build Call record ──────────────────────────────────────────────
        duration_ms: int | None = None
        if webhook_call.start_timestamp and webhook_call.end_timestamp:
            duration_ms = webhook_call.end_timestamp - webhook_call.start_timestamp

        call = Call(
            tenant_id=tenant_id,
            contact_id=contact.id if contact else None,
            retell_call_id=webhook_call.call_id,
            call_direction=webhook_call.direction,
            agent_used=webhook_call.agent_id,
            transcript=webhook_call.transcript,
            recording_url=webhook_call.recording_url,  # scrubbed URL set in webhooks.py
            summary=analysis_dict.get("call_summary"),
            patient_sentiment=analysis_dict.get("user_sentiment"),
            call_status=self._map_call_status(webhook_call, analysis_dict),
            patient_status=(
                PatientStatus.CONTACTED.value
                if webhook_call.direction == "outbound"
                else PatientStatus.NOT_CONTACTED.value
            ),
            call_duration_seconds=(duration_ms // 1000) if duration_ms else None,
            is_new_patient=contact.is_new_patient if contact else is_new_patient_flag,
            is_complaint=bool(custom.get("Complaining Patient", False)),
            is_insurance_billing=bool(custom.get("Insurance and Billing", False)),
            # Treat the string "None" (from Retell when no detail exists) as NULL
            next_action=_nonempty(custom.get("Appointment Detail")),
        )

        if webhook_call.start_timestamp:
            start_dt = datetime.fromtimestamp(
                webhook_call.start_timestamp / 1000, tz=timezone.utc
            )
            call.call_date = start_dt.date()
            call.call_time = start_dt.timetz()  # keep UTC offset — column is TIME WITH TIME ZONE
        else:
            now = datetime.now(timezone.utc)
            call.call_date = now.date()
            call.call_time = now.timetz()  # keep UTC offset

        self.session.add(call)

        logger.info(
            f"Saved Call {webhook_call.call_id} for tenant {tenant_id} "
            f"(contact={'found' if contact and contact.id else 'unknown'})"
        )

        # Caller (webhooks.py) is responsible for session.commit()
        return call
