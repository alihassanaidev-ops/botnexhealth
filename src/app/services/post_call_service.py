"""Service for processing post-call data from Retell webhooks."""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.call import Call, CallStatus, PatientStatus
from src.app.models.contact import Contact
from src.app.models.contact_location_access import ContactLocationAccess
from src.app.retell.models import RetellCallData
from src.app.services.custom_field_service import CustomFieldService
from src.app.services.sms_privacy import hash_for_logging

logger = logging.getLogger(__name__)

# ── Status normalization ───────────────────────────────────────────────────────

# Maps Retell's Title Case "Call Status" values to our snake_case CallStatus enum values.
# Keys are lowercased for case-insensitive matching.
RETELL_STATUS_MAP: dict[str, str] = {
    "appointment booked": CallStatus.APPOINTMENT_BOOKED.value,
    "appointment rescheduled": CallStatus.APPOINTMENT_RESCHEDULED.value,
    "appointment cancelled": CallStatus.APPOINTMENT_CANCELLED.value,
    "emergency": CallStatus.EMERGENCY.value,
    "complaint": CallStatus.COMPLAINT.value,
    "needs callback": CallStatus.NEEDS_CALLBACK.value,
    "faq handled": CallStatus.FAQ_HANDLED.value,
    "financial inquiry": CallStatus.FINANCIAL_INQUIRY.value,
    "transferred": CallStatus.TRANSFERRED.value,
    "insurance verified": CallStatus.INSURANCE_VERIFIED.value,
    "insurance unverified": CallStatus.INSURANCE_UNVERIFIED.value,
    "no action needed": CallStatus.NO_ACTION_NEEDED.value,
}


def _nonempty(value: str | None) -> str | None:
    """Return None for falsy or placeholder strings like 'None', 'N/A', 'n/a'."""
    if not value:
        return None
    stripped = value.strip()
    if stripped.lower() in ("none", "n/a", ""):
        return None
    return stripped


def _parse_dob(raw: str | None) -> str | None:
    """Normalize DOB to ISO YYYY-MM-DD.

    Handles both ISO format ("2001-02-02") and human-readable format
    ("February 2, 2001") that Retell may send.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw.lower() in ("none", "n/a"):
        return None
    # Already ISO
    try:
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    except ValueError:
        pass
    # Human-readable: "February 2, 2001" or "February 02, 2001"
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # DOB is a HIPAA §164.514(b)(2)(i)(C) identifier — log only the keyed
    # hash and length so operators can correlate without seeing the value.
    logger.warning(
        "Could not parse DOB string: dob_hash=%s len=%d",
        hash_for_logging(raw),
        len(raw),
    )
    return None


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

    def _parse_call_tags(
        self, custom: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        """Parse the 'Call Status' CSV field into (primary_status, all_tags_csv).

        Retell sends a comma-separated Title Case string, e.g. "Complaint, FAQ Handled".
        We normalize each token to a snake_case enum value and return:
          - primary_status: first recognized tag (stored indexed for fast filtering)
          - all_tags_csv:   all recognized tags joined by comma (stored for display/multi-filter)

        Unknown tokens are skipped with a warning so new Retell statuses don't crash us.
        """
        raw = (custom.get("Call Status") or "").strip()
        if not raw:
            return None, None

        tags: list[str] = []
        for part in raw.split(","):
            token = part.strip().lower()
            mapped = RETELL_STATUS_MAP.get(token)
            if mapped:
                tags.append(mapped)
            else:
                logger.warning("Unrecognized Retell 'Call Status' token: %r — skipping", token)

        if not tags:
            return None, None

        return tags[0], ",".join(tags)

    @staticmethod
    def _extract_name(
        custom: dict[str, Any],
        dynamic_vars: dict[str, Any],
    ) -> tuple[str | None, str | None, str | None]:
        """Return (first_name, last_name, full_name) from available sources.

        Priority:
          1. Separate first_name / last_name in collected_dynamic_variables
          2. Separate first_name / last_name in custom_analysis_data
          3. Combined 'Patient name' string in custom_analysis_data

        _nonempty() is applied to each source individually before the `or` so that
        placeholder strings like "None" / "N/A" correctly fall through to the next source.
        """
        first = (
            _nonempty(dynamic_vars.get("first_name"))
            or _nonempty(custom.get("first_name"))
        )
        last = (
            _nonempty(dynamic_vars.get("last_name"))
            or _nonempty(custom.get("last_name"))
        )

        if first:
            full = f"{first} {last}".strip() if last else first
            return first, last, full

        # Fall back to combined "Patient name"
        combined = _nonempty(custom.get("Patient name"))
        if combined:
            parts = combined.split(None, 1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else None
            return first, last, combined

        return None, None, None

    @staticmethod
    def _extract_patient_id(
        custom: dict[str, Any],
        dynamic_vars: dict[str, Any],
    ) -> str | None:
        """Extract NexHealth patient ID from webhook data.

        The Retell agent stores the patient_id in dynamic variables after
        a successful lookup_patient or create_patient call. We also check
        custom_analysis_data as a fallback.
        """
        return (
            _nonempty(dynamic_vars.get("patient_id"))
            or _nonempty(dynamic_vars.get("nexhealth_patient_id"))
            or _nonempty(custom.get("patient_id"))
            or _nonempty(custom.get("nexhealth_patient_id"))
        )

    async def process_call_analyzed_event(
        self,
        institution_id: str,
        webhook_call: RetellCallData,
        analysis: dict[str, Any] | None,
        location_id: str | None = None,
    ) -> Call:
        """
        Process a call_analyzed event: upsert Contact and create Call.

        Idempotency: retell_call_id is UNIQUE — duplicate webhooks are safe.
        """
        analysis_dict = analysis or {}
        custom: dict[str, Any] = analysis_dict.get("custom_analysis_data") or {}
        dynamic_vars: dict[str, Any] = analysis_dict.get("collected_dynamic_variables") or {}

        # ── 1. Resolve / create Contact ──────────────────────────────────────
        phone = self._determine_patient_phone(webhook_call)
        contact: Contact | None = None

        first_name, last_name, full_name = self._extract_name(custom, dynamic_vars)
        patient_email: str | None = (
            _nonempty(dynamic_vars.get("email"))
            or _nonempty(custom.get("Patient email"))
            or _nonempty(custom.get("email"))
        )
        patient_dob: str | None = (
            _parse_dob(dynamic_vars.get("date_of_birth"))
            or _parse_dob(dynamic_vars.get("dob"))
            or _parse_dob(custom.get("Date of birth"))
        )
        is_new_patient_flag: bool = bool(custom.get("New_patient", False))

        # Extract NexHealth patient ID from webhook data
        pms_patient_id: str | None = self._extract_patient_id(custom, dynamic_vars)

        if pms_patient_id:
            # ── Primary path: resolve by PMS Patient ID (unique per institution) ──
            existing = (
                await self.session.execute(
                    select(Contact).where(
                        Contact.institution_id == institution_id,
                        Contact.nexhealth_patient_id == pms_patient_id,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                contact = existing
                contact.last_agent_interaction_id = webhook_call.agent_id
                # Always update identity fields from the latest call data
                if first_name:
                    contact.first_name = first_name
                    contact.last_name = last_name
                    contact.full_name = full_name
                if patient_email:
                    contact.email = patient_email
                if patient_dob:
                    contact.date_of_birth = patient_dob
                if phone:
                    contact.phone = phone
            else:
                # New PMS-linked contact
                contact = Contact(
                    institution_id=institution_id,
                    first_name=first_name,
                    last_name=last_name,
                    full_name=full_name,
                    nexhealth_patient_id=pms_patient_id,
                    is_new_patient=is_new_patient_flag,
                    last_agent_interaction_id=webhook_call.agent_id,
                )
                if phone:
                    contact.phone = phone
                if patient_email:
                    contact.email = patient_email
                if patient_dob:
                    contact.date_of_birth = patient_dob
                self.session.add(contact)
                await self.session.flush()
        else:
            # ── Fallback: no PMS ID — create a new Contact per call ──────
            # We intentionally do NOT reuse by phone_hash here because
            # we cannot know which patient is calling from a shared phone.
            # Example: Mother (Jane) calling for her son (Timmy).
            contact = Contact(
                institution_id=institution_id,
                first_name=first_name,
                last_name=last_name,
                full_name=full_name,
                is_new_patient=is_new_patient_flag,
                last_agent_interaction_id=webhook_call.agent_id,
            )
            if phone:
                contact.phone = phone
            if patient_email:
                contact.email = patient_email
            if patient_dob:
                contact.date_of_birth = patient_dob
            self.session.add(contact)
            await self.session.flush()

        if contact and location_id:
            await self.session.execute(
                pg_insert(ContactLocationAccess)
                .values(
                    institution_id=institution_id,
                    contact_id=contact.id,
                    location_id=location_id,
                )
                .on_conflict_do_nothing(
                    index_elements=["contact_id", "location_id"],
                )
            )

        # ── 2. Resolve call status tags ───────────────────────────────────────
        primary_status, all_tags = self._parse_call_tags(custom)

        # ── 3. Build Call record ──────────────────────────────────────────────
        duration_ms: int | None = None
        if webhook_call.start_timestamp and webhook_call.end_timestamp:
            duration_ms = webhook_call.end_timestamp - webhook_call.start_timestamp

        call = Call(
            institution_id=institution_id,
            contact_id=contact.id if contact else None,
            location_id=location_id,
            retell_call_id=webhook_call.call_id,
            call_direction=webhook_call.direction,
            agent_used=webhook_call.agent_id,
            recording_url=webhook_call.recording_url,  # scrubbed URL set in webhooks.py
            patient_sentiment=analysis_dict.get("user_sentiment"),
            call_status=primary_status,
            call_tags=all_tags,
            patient_status=(
                PatientStatus.CONTACTED.value
                if webhook_call.direction == "outbound"
                else PatientStatus.NOT_CONTACTED.value
            ),
            call_duration_seconds=(duration_ms // 1000) if duration_ms else None,
            is_new_patient=contact.is_new_patient if contact else is_new_patient_flag,
            is_complaint=primary_status == CallStatus.COMPLAINT.value
                or (all_tags is not None and "complaint" in all_tags),
            is_insurance_billing=(
                primary_status in (
                    CallStatus.INSURANCE_VERIFIED.value,
                    CallStatus.INSURANCE_UNVERIFIED.value,
                )
                or bool(custom.get("Insurance and Billing", False))
            ),
            # Treat the string "None" (from Retell when no detail exists) as NULL
            next_action=_nonempty(custom.get("Appointment Detail")),
        )
        # Encrypted setters — write after construction so JSON serialization
        # happens through the property and never raw onto the column.
        call.transcript_with_tool_calls = webhook_call.transcript_with_tool_calls
        call.summary = analysis_dict.get("call_summary")

        if webhook_call.start_timestamp:
            start_dt = datetime.fromtimestamp(
                webhook_call.start_timestamp / 1000, tz=timezone.utc
            )
            call.call_date = start_dt.date()
            call.call_time = start_dt.timetz()  # keep UTC offset — column is TIME WITH TIME ZONE
        else:
            now = datetime.now(timezone.utc)
            call.call_date = now.date()
            call.call_time = now.timetz()

        self.session.add(call)
        await self.session.flush()  # ensure call.id is assigned

        # ── 4. Extract institution-defined custom fields from webhook data ─────
        cf_service = CustomFieldService(self.session)
        cf_count = await cf_service.extract_and_save_from_webhook(
            institution_id=institution_id,
            call_id=call.id,
            custom_analysis_data=custom,
            collected_dynamic_variables=dynamic_vars,
        )

        logger.info(
            "Saved Call %s for institution %s (contact=%s, status=%s, tags=%s, custom_fields=%d)",
            webhook_call.call_id,
            institution_id,
            "found" if contact and contact.id else "unknown",
            primary_status,
            all_tags,
            cf_count,
        )

        # Caller (webhooks.py) is responsible for session.commit()
        return call
