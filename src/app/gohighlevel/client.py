"""GoHighLevel API client for contact sync."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.app.gohighlevel.models import GHLContactUpsert, GHLCustomField

logger = logging.getLogger(__name__)


class GHLClient:
    """Client for GoHighLevel API."""

    BASE_URL = "https://services.leadconnectorhq.com"
    API_VERSION = "2021-07-28"

    # GHL Custom Field IDs (configured for this integration)
    FIELD_CALL_SUMMARY = "0TQcbUJNGUbRvQoWV0eu"
    FIELD_APPOINTMENT_DETAILS = "Hh1cBXE5ftlWRJcisHw2"
    FIELD_RECORDING_LINK = "Hsp33AaWTQIrBMvxQPzi"
    FIELD_CALL_DURATION = "SNE1VSlod5kIhjBl9ORq"
    FIELD_TRANSCRIPT = "eaAIjN3dy90b5JQciZUj"

    def __init__(self, api_key: str, location_id: str):
        """
        Initialize GHL client.

        Args:
            api_key: GoHighLevel API key (pit-xxx format)
            location_id: GHL location ID
        """
        self.api_key = api_key
        self.location_id = location_id
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Version": self.API_VERSION,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def format_duration(duration_ms: int) -> str:
        """Format duration from milliseconds to human readable string."""
        seconds = duration_ms // 1000
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        if minutes > 0:
            return f"{minutes}m {remaining_seconds}s"
        return f"{remaining_seconds}s"

    async def upsert_contact_from_retell(
        self,
        phone_number: str,
        call_summary: str | None = None,
        appointment_details: str | None = None,
        recording_url: str | None = None,
        duration_ms: int | None = None,
        transcript: str | None = None,
        patient_name: str | None = None,
        patient_dob: str | None = None,
        patient_email: str | None = None,
    ) -> dict[str, Any]:
        """
        Create or update a contact in GHL from Retell call data.

        Args:
            phone_number: Caller's phone number
            call_summary: AI-generated call summary
            appointment_details: Extracted appointment details
            recording_url: URL to call recording
            duration_ms: Call duration in milliseconds
            transcript: Full call transcript
            patient_name: Extracted patient name (if available)
            patient_dob: Extracted patient date of birth (if available)
            patient_email: Extracted patient email (if available)

        Returns:
            GHL API response
        """
        client = await self._get_client()

        # Build custom fields list
        custom_fields: list[GHLCustomField] = []

        if call_summary:
            custom_fields.append(GHLCustomField(
                id=self.FIELD_CALL_SUMMARY,
                field_value=call_summary
            ))

        if appointment_details:
            custom_fields.append(GHLCustomField(
                id=self.FIELD_APPOINTMENT_DETAILS,
                field_value=appointment_details
            ))

        if recording_url:
            custom_fields.append(GHLCustomField(
                id=self.FIELD_RECORDING_LINK,
                field_value=recording_url
            ))

        if duration_ms is not None:
            custom_fields.append(GHLCustomField(
                id=self.FIELD_CALL_DURATION,
                field_value=self.format_duration(duration_ms)
            ))

        if transcript:
            # GHL might have field length limits, truncate if needed
            truncated_transcript = transcript[:10000] if len(transcript) > 10000 else transcript
            custom_fields.append(GHLCustomField(
                id=self.FIELD_TRANSCRIPT,
                field_value=truncated_transcript
            ))

        # Build contact payload
        contact = GHLContactUpsert(
            locationId=self.location_id,
            phone=phone_number,
            name=patient_name if patient_name else None,
            dateOfBirth=patient_dob if patient_dob else None,
            email=patient_email if patient_email else None,
            customFields=custom_fields,
        )

        # Log (without sensitive data)
        logger.info(
            f"Upserting GHL contact: phone={phone_number[-4:] if len(phone_number) > 4 else '****'}, "
            f"fields={len(custom_fields)}"
        )

        try:
            response = await client.post(
                "/contacts/upsert",
                json=contact.model_dump(exclude_none=True),
            )
            response.raise_for_status()
            result = response.json()

            contact_id = result.get("contact", {}).get("id", "unknown")
            is_new = result.get("new", False)
            logger.info(f"GHL contact upserted: id={contact_id}, new={is_new}")

            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"GHL API error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"GHL client error: {e}")
            raise
