"""NexHealth v3 shadow webhook capture and subscription lifecycle."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.nexhealth_webhook_shadow import (
    NexHealthWebhookShadowEvent,
    NexHealthWebhookShadowParseStatus,
    NexHealthWebhookShadowSubscription,
    NexHealthWebhookShadowSubscriptionStatus,
)
from src.app.nexhealth.api_contract import NexHealthAPIContract
from src.app.services.automation.nexhealth_subscription_service import (
    DEFAULT_APPOINTMENT_EVENTS,
    DEFAULT_PATIENT_EVENTS,
    DEFAULT_SYNC_STATUS_EVENTS,
    _resource_type_for_event,
)
from src.app.services.retention_policy import default_nexhealth_webhook_raw_retain_until
from src.app.services.sms_privacy import (
    payload_hash,
    redact_payload,
    sanitize_provider_error,
)

logger = logging.getLogger(__name__)

SHADOW_ROUTE_APPOINTMENTS = "appointments"
SHADOW_ROUTE_PATIENTS = "patients"
SHADOW_ROUTE_SYNC_STATUS = "sync_status"

SHADOW_ROUTE_EVENT_TYPES = {
    SHADOW_ROUTE_APPOINTMENTS: DEFAULT_APPOINTMENT_EVENTS,
    SHADOW_ROUTE_PATIENTS: DEFAULT_PATIENT_EVENTS,
    SHADOW_ROUTE_SYNC_STATUS: DEFAULT_SYNC_STATUS_EVENTS,
}
SHADOW_ROUTE_PATHS = {
    SHADOW_ROUTE_APPOINTMENTS: "appointments",
    SHADOW_ROUTE_PATIENTS: "patients",
    SHADOW_ROUTE_SYNC_STATUS: "sync-status",
}

_DELIVERY_ID_HEADERS = (
    "x-nexhealth-delivery-id",
    "x-nexhealth-event-id",
    "x-request-id",
)


@dataclass(frozen=True)
class ParsedShadowPayload:
    payload: dict[str, Any] | None
    parse_error: Exception | None

    @property
    def parse_status(self) -> NexHealthWebhookShadowParseStatus:
        if self.parse_error is not None or self.payload is None:
            return NexHealthWebhookShadowParseStatus.FAILED
        return NexHealthWebhookShadowParseStatus.PARSED


@dataclass(frozen=True)
class ShadowCaptureResult:
    row: NexHealthWebhookShadowEvent

    @property
    def parse_status(self) -> str:
        return self.row.parse_status


@dataclass(frozen=True)
class ShadowSubscriptionHealthSummary:
    total: int = 0
    active: int = 0
    pending: int = 0
    disabled: int = 0
    failed: int = 0


@dataclass(frozen=True)
class _StableV3NexHealthConfig:
    api_key: str
    base_url: str
    nexhealth_max_keepalive_connections: int = 10
    nexhealth_max_connections: int = 20

    @property
    def accept_header(self) -> str:
        return NexHealthAPIContract.STABLE_V3.accept_header

    @property
    def api_version(self) -> str:
        return NexHealthAPIContract.STABLE_V3.api_version_header

    @property
    def nexhealth_api_contract(self) -> NexHealthAPIContract:
        return NexHealthAPIContract.STABLE_V3


def parse_shadow_payload(raw_body: bytes) -> ParsedShadowPayload:
    """Parse JSON without raising so shadow validation can still return 2xx."""
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - stored as shadow parse evidence.
        return ParsedShadowPayload(payload=None, parse_error=exc)
    if not isinstance(payload, dict):
        return ParsedShadowPayload(
            payload=None,
            parse_error=ValueError("NexHealth shadow payload root is not an object"),
        )
    return ParsedShadowPayload(payload=payload, parse_error=None)


def shadow_callback_url(callback_base_url: str, route_family: str) -> str:
    path = SHADOW_ROUTE_PATHS[route_family]
    return f"{callback_base_url.rstrip('/')}/api/v1/nexhealth/webhooks/shadow/{path}"


class NexHealthWebhookShadowCaptureService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def capture(
        self,
        *,
        route_family: str,
        raw_payload: str,
        parsed: ParsedShadowPayload,
        headers: dict[str, str] | None = None,
    ) -> ShadowCaptureResult:
        now = datetime.now(timezone.utc)
        payload = parsed.payload
        parse_status = parsed.parse_status
        parse_error_summary = (
            sanitize_provider_error(parsed.parse_error)
            if parsed.parse_error is not None
            else None
        )

        identity = _extract_identity(payload, route_family) if payload is not None else {}
        resolution = (
            await self._resolve_delivery(payload, route_family)
            if payload is not None
            else _empty_resolution()
        )
        payload_for_hash: Any = payload if payload is not None else raw_payload
        redacted = redact_payload(payload) if payload is not None else {"payload": "[redacted]"}
        if not isinstance(redacted, dict):
            redacted = {"payload": redacted}

        row = NexHealthWebhookShadowEvent(
            id=str(uuid4()),
            institution_id=resolution.get("institution_id"),
            location_id=resolution.get("location_id"),
            api_contract=NexHealthAPIContract.STABLE_V3.value,
            route_family=route_family,
            subdomain=_clean_str(payload.get("subdomain")) if payload else None,
            nexhealth_location_id=resolution.get("nexhealth_location_id"),
            resource_type=identity.get("resource_type"),
            event_name=identity.get("event_name"),
            event_family=identity.get("event_family"),
            pms_resource_id=identity.get("pms_resource_id"),
            change_marker=identity.get("change_marker"),
            business_event_key=identity.get("business_event_key"),
            provider_delivery_id=_provider_delivery_id(payload, headers or {}),
            provider_subscription_id=_provider_subscription_id(payload),
            payload_hash=payload_hash(payload_for_hash),
            parse_status=parse_status.value,
            parse_error_summary=parse_error_summary,
            resolution_status=str(resolution["resolution_status"]),
            resolution_metadata=resolution.get("resolution_metadata"),
            extracted_identity=identity or None,
            raw_payload_retain_until=default_nexhealth_webhook_raw_retain_until(now),
            updated_at=now,
        )
        row.raw_payload = raw_payload
        row.redacted_payload = redacted
        self.session.add(row)
        await self.session.flush()
        await self._record_subscription_capture(row, resolution)
        return ShadowCaptureResult(row=row)

    async def _resolve_delivery(
        self, payload: dict[str, Any], route_family: str
    ) -> dict[str, Any]:
        subdomain = _clean_str(payload.get("subdomain"))
        nexhealth_location_ids = _payload_location_ids(payload, route_family)
        locations = await self._query_locations(
            subdomain=subdomain,
            nexhealth_location_ids=nexhealth_location_ids,
        )
        return _resolution_from_locations(
            locations=locations,
            nexhealth_location_ids=nexhealth_location_ids,
        )

    async def _query_locations(
        self, *, subdomain: str | None, nexhealth_location_ids: list[str]
    ) -> list[InstitutionLocation]:
        if not subdomain and not nexhealth_location_ids:
            return []
        stmt = (
            select(InstitutionLocation)
            .join(Institution, Institution.id == InstitutionLocation.institution_id)
            .where(
                Institution.pms_type == "nexhealth",
                InstitutionLocation.nexhealth_location_id.is_not(None),
            )
        )
        if subdomain:
            stmt = stmt.where(InstitutionLocation.nexhealth_subdomain == subdomain)
        if nexhealth_location_ids:
            stmt = stmt.where(
                InstitutionLocation.nexhealth_location_id.in_(nexhealth_location_ids)
            )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _record_subscription_capture(
        self, row: NexHealthWebhookShadowEvent, resolution: dict[str, Any]
    ) -> None:
        location_ids = list(resolution.get("matched_location_ids") or [])
        if not location_ids and row.location_id:
            location_ids = [row.location_id]
        if not row.institution_id or not location_ids:
            return

        result = await self.session.execute(
            select(NexHealthWebhookShadowSubscription).where(
                NexHealthWebhookShadowSubscription.institution_id == row.institution_id,
                NexHealthWebhookShadowSubscription.location_id.in_(location_ids),
                NexHealthWebhookShadowSubscription.route_family == row.route_family,
            )
        )
        subscriptions = list(result.scalars().all())
        now = datetime.now(timezone.utc)
        for subscription in subscriptions:
            subscription.last_event_at = now
            subscription.last_health_check_at = now
            subscription.last_shadow_capture_id = row.id
            if row.parse_status == NexHealthWebhookShadowParseStatus.PARSED.value:
                subscription.last_parse_success_at = now
                subscription.parse_success_count += 1
                if subscription.provider_endpoint_id or subscription.provider_subscription_ids:
                    subscription.status = NexHealthWebhookShadowSubscriptionStatus.ACTIVE.value
            else:
                subscription.last_parse_failure_at = now
                subscription.parse_failure_count += 1
            subscription.updated_at = now


class NexHealthWebhookShadowSubscriptionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ensure_for_configured_locations(
        self,
        *,
        callback_base_url: str | None = None,
        route_families: list[str] | None = None,
    ) -> dict[str, int]:
        result = await self.session.execute(
            select(InstitutionLocation, Institution)
            .join(Institution, Institution.id == InstitutionLocation.institution_id)
            .where(
                Institution.pms_type == "nexhealth",
                InstitutionLocation.nexhealth_subdomain.is_not(None),
                InstitutionLocation.nexhealth_location_id.is_not(None),
            )
        )
        created = 0
        updated = 0
        activated = 0
        failed = 0
        for location, institution in result.all():
            for route_family in route_families or list(SHADOW_ROUTE_EVENT_TYPES):
                row, was_created = await self.ensure_location_subscription(
                    institution=institution,
                    location=location,
                    route_family=route_family,
                    callback_base_url=callback_base_url,
                )
                created += int(was_created)
                updated += int(not was_created)
                activated += int(
                    row.status == NexHealthWebhookShadowSubscriptionStatus.ACTIVE.value
                )
                failed += int(
                    row.status == NexHealthWebhookShadowSubscriptionStatus.FAILED.value
                )
        return {
            "created": created,
            "updated": updated,
            "activated": activated,
            "failed": failed,
        }

    async def ensure_location_subscription(
        self,
        *,
        institution: Institution,
        location: InstitutionLocation,
        route_family: str,
        callback_base_url: str | None = None,
    ) -> tuple[NexHealthWebhookShadowSubscription, bool]:
        if route_family not in SHADOW_ROUTE_EVENT_TYPES:
            raise ValueError(f"Unsupported NexHealth shadow route: {route_family}")
        institution_id = str(institution.id)
        location_id = str(location.id)
        events = SHADOW_ROUTE_EVENT_TYPES[route_family]
        callback_url = (
            shadow_callback_url(callback_base_url, route_family)
            if callback_base_url
            else None
        )
        existing = (
            await self.session.execute(
                select(NexHealthWebhookShadowSubscription).where(
                    NexHealthWebhookShadowSubscription.institution_id == institution_id,
                    NexHealthWebhookShadowSubscription.location_id == location_id,
                    NexHealthWebhookShadowSubscription.route_family == route_family,
                )
            )
        ).scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if existing is None:
            existing = NexHealthWebhookShadowSubscription(
                id=str(uuid4()),
                institution_id=institution_id,
                location_id=location_id,
                route_family=route_family,
                subdomain=str(location.nexhealth_subdomain),
                nexhealth_location_id=str(location.nexhealth_location_id),
                callback_url=callback_url,
                event_types=list(events),
                api_contract=NexHealthAPIContract.STABLE_V3.value,
                status=NexHealthWebhookShadowSubscriptionStatus.PENDING.value,
                updated_at=now,
            )
            self.session.add(existing)
            was_created = True
        else:
            existing.subdomain = str(location.nexhealth_subdomain)
            existing.nexhealth_location_id = str(location.nexhealth_location_id)
            existing.callback_url = callback_url
            existing.event_types = list(events)
            existing.api_contract = NexHealthAPIContract.STABLE_V3.value
            existing.updated_at = now
            if existing.status == NexHealthWebhookShadowSubscriptionStatus.DISABLED.value:
                existing.status = NexHealthWebhookShadowSubscriptionStatus.PENDING.value
            was_created = False

        if callback_url and not existing.provider_endpoint_id:
            await self._try_remote_create(
                row=existing,
                institution=institution,
                location=location,
                callback_url=callback_url,
                event_types=list(events),
            )
        return existing, was_created

    async def health_check(self) -> ShadowSubscriptionHealthSummary:
        result = await self.session.execute(select(NexHealthWebhookShadowSubscription))
        rows = list(result.scalars().all())
        counts = {
            NexHealthWebhookShadowSubscriptionStatus.ACTIVE.value: 0,
            NexHealthWebhookShadowSubscriptionStatus.PENDING.value: 0,
            NexHealthWebhookShadowSubscriptionStatus.DISABLED.value: 0,
            NexHealthWebhookShadowSubscriptionStatus.FAILED.value: 0,
        }
        now = datetime.now(timezone.utc)
        for row in rows:
            row.last_health_check_at = now
            row.updated_at = now
            counts[row.status] = counts.get(row.status, 0) + 1
        return ShadowSubscriptionHealthSummary(
            total=len(rows),
            active=counts[NexHealthWebhookShadowSubscriptionStatus.ACTIVE.value],
            pending=counts[NexHealthWebhookShadowSubscriptionStatus.PENDING.value],
            disabled=counts[NexHealthWebhookShadowSubscriptionStatus.DISABLED.value],
            failed=counts[NexHealthWebhookShadowSubscriptionStatus.FAILED.value],
        )

    async def _try_remote_create(
        self,
        *,
        row: NexHealthWebhookShadowSubscription,
        institution: Institution,
        location: InstitutionLocation,
        callback_url: str,
        event_types: list[str],
    ) -> None:
        from src.app.api.helpers import handle_nexhealth_request
        from src.app.config import settings
        from src.app.nexhealth.client import NexHealthClient

        if not settings.nexhealth_api_key:
            row.status = NexHealthWebhookShadowSubscriptionStatus.FAILED.value
            row.error_metadata = {"reason": "missing_nexhealth_api_key"}
            return

        config = _StableV3NexHealthConfig(
            api_key=settings.nexhealth_api_key,
            base_url=settings.nexhealth_base_url,
            nexhealth_max_keepalive_connections=settings.nexhealth_max_keepalive_connections,
            nexhealth_max_connections=settings.nexhealth_max_connections,
        )
        subscription_ids: list[str] = []
        try:
            async with NexHealthClient(config) as client:
                endpoint = await handle_nexhealth_request(
                    client,
                    "POST",
                    "/webhook_endpoints",
                    json={"target_url": callback_url},
                )
                endpoint_id = _extract_endpoint_id(endpoint)
                if not endpoint_id:
                    raise RuntimeError("webhook_endpoint id missing in response")

                for event in event_types:
                    subscription = await handle_nexhealth_request(
                        client,
                        "POST",
                        f"/webhook_endpoints/{endpoint_id}/webhook_subscriptions",
                        params={"subdomain": str(location.nexhealth_subdomain)},
                        json={
                            "resource_type": _resource_type_for_event(event),
                            "event": event,
                            "active": True,
                        },
                    )
                    subscription_id = _extract_provider_subscription_id(subscription)
                    if subscription_id:
                        subscription_ids.append(subscription_id)
        except Exception as exc:  # noqa: BLE001
            row.status = NexHealthWebhookShadowSubscriptionStatus.FAILED.value
            row.error_metadata = {
                "type": type(exc).__name__,
                "reason": "shadow_subscription_create_failed",
            }
            logger.warning(
                "nexhealth shadow subscription create failed institution=%s location=%s route=%s type=%s",
                institution.id,
                location.id,
                row.route_family,
                type(exc).__name__,
            )
            return

        row.provider_endpoint_id = str(endpoint_id)
        row.provider_subscription_ids = subscription_ids
        row.status = NexHealthWebhookShadowSubscriptionStatus.ACTIVE.value
        row.error_metadata = None


def _clean_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _empty_resolution() -> dict[str, Any]:
    return {
        "institution_id": None,
        "location_id": None,
        "nexhealth_location_id": None,
        "resolution_status": "unresolved",
        "resolution_metadata": {
            "matched_institution_ids": [],
            "matched_location_ids": [],
            "nexhealth_location_ids": [],
        },
        "matched_location_ids": [],
    }


def _resolution_from_locations(
    *, locations: list[InstitutionLocation], nexhealth_location_ids: list[str]
) -> dict[str, Any]:
    institution_ids = sorted({str(location.institution_id) for location in locations})
    location_ids = sorted({str(location.id) for location in locations})
    metadata = {
        "matched_institution_ids": institution_ids,
        "matched_location_ids": location_ids,
        "nexhealth_location_ids": nexhealth_location_ids,
    }
    if not locations:
        return {
            **_empty_resolution(),
            "nexhealth_location_id": nexhealth_location_ids[0] if nexhealth_location_ids else None,
            "resolution_metadata": metadata,
        }
    if len(institution_ids) > 1:
        return {
            "institution_id": None,
            "location_id": None,
            "nexhealth_location_id": nexhealth_location_ids[0] if nexhealth_location_ids else None,
            "resolution_status": "ambiguous",
            "resolution_metadata": metadata,
            "matched_location_ids": location_ids,
        }
    status = "resolved" if len(location_ids) == 1 else "multiple_locations"
    return {
        "institution_id": institution_ids[0],
        "location_id": location_ids[0] if len(location_ids) == 1 else None,
        "nexhealth_location_id": nexhealth_location_ids[0] if nexhealth_location_ids else None,
        "resolution_status": status,
        "resolution_metadata": metadata,
        "matched_location_ids": location_ids,
    }


def _extract_identity(payload: dict[str, Any], route_family: str) -> dict[str, Any]:
    event_name = _clean_str(payload.get("event_name") or payload.get("event"))
    event_family = event_name.split(".", 1)[0] if event_name else None
    resource = _clean_str(payload.get("resource_type")) or _route_resource_type(route_family)
    resource_payload = _primary_resource_payload(payload, route_family)
    pms_resource_id = _clean_str(resource_payload.get("id")) if resource_payload else None
    if route_family == SHADOW_ROUTE_SYNC_STATUS and not pms_resource_id:
        subdomain = _clean_str(payload.get("subdomain")) or "unknown-subdomain"
        location_ids = _sync_status_location_ids(resource_payload or {})
        pms_resource_id = f"{subdomain}:{','.join(location_ids)}"
    change_marker = _change_marker(payload, resource_payload)
    business_event_key = None
    if resource and pms_resource_id and event_family:
        business_event_key = f"{resource}:{pms_resource_id}:{event_family}:{change_marker or 'none'}"
    return {
        "resource_type": resource,
        "event_name": event_name,
        "event_family": event_family,
        "pms_resource_id": pms_resource_id,
        "change_marker": change_marker,
        "business_event_key": business_event_key,
    }


def _route_resource_type(route_family: str) -> str | None:
    if route_family == SHADOW_ROUTE_APPOINTMENTS:
        return "Appointment"
    if route_family == SHADOW_ROUTE_PATIENTS:
        return "Patient"
    if route_family == SHADOW_ROUTE_SYNC_STATUS:
        return "SyncStatus"
    return None


def _primary_resource_payload(
    payload: dict[str, Any], route_family: str
) -> dict[str, Any] | None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if route_family == SHADOW_ROUTE_APPOINTMENTS:
        appointment = data.get("appointment")
        if isinstance(appointment, dict):
            return appointment
        appointments = data.get("appointments")
        if isinstance(appointments, list):
            return next((item for item in appointments if isinstance(item, dict)), None)
    if route_family == SHADOW_ROUTE_PATIENTS:
        patient = data.get("patient") or data.get("user")
        if isinstance(patient, dict):
            return patient
        patients = data.get("patients") or data.get("users")
        if isinstance(patients, list):
            return next((item for item in patients if isinstance(item, dict)), None)
        if data.get("id") is not None:
            return data
    if route_family == SHADOW_ROUTE_SYNC_STATUS:
        for key in ("sync_status", "syncstatus"):
            value = data.get(key)
            if isinstance(value, dict):
                return value
        for key in ("sync_statuses", "syncstatuses"):
            value = data.get(key)
            if isinstance(value, list):
                return next((item for item in value if isinstance(item, dict)), None)
        return data if data else payload
    return None


def _change_marker(
    payload: dict[str, Any], resource_payload: dict[str, Any] | None
) -> str | None:
    source = resource_payload or {}
    for key in (
        "updated_at",
        "start_time",
        "cancelled",
        "canceled",
        "read_status_at",
        "write_status_at",
        "event_time",
    ):
        value = source.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"
    event_time = payload.get("event_time")
    return f"event_time:{event_time}" if event_time not in (None, "") else None


def _payload_location_ids(payload: dict[str, Any], route_family: str) -> list[str]:
    resource_payload = _primary_resource_payload(payload, route_family)
    if not resource_payload:
        return []
    if route_family == SHADOW_ROUTE_SYNC_STATUS:
        return _sync_status_location_ids(resource_payload)
    if route_family == SHADOW_ROUTE_PATIENTS:
        return _patient_location_ids(resource_payload)
    value = resource_payload.get("location_id")
    return [str(value)] if value not in (None, "") else []


def _patient_location_ids(patient: dict[str, Any]) -> list[str]:
    values = patient.get("location_ids") or patient.get("locations") or []
    if isinstance(values, list):
        ids = []
        for value in values:
            if isinstance(value, dict):
                value = value.get("id")
            if value not in (None, ""):
                ids.append(str(value))
        return ids
    value = patient.get("location_id")
    return [str(value)] if value not in (None, "") else []


def _sync_status_location_ids(status_payload: dict[str, Any]) -> list[str]:
    values = status_payload.get("locations") or status_payload.get("location_ids") or []
    if isinstance(values, list):
        ids = []
        for value in values:
            if isinstance(value, dict):
                value = value.get("id") or value.get("location_id")
            if value not in (None, ""):
                ids.append(str(value))
        return ids
    value = status_payload.get("location_id")
    return [str(value)] if value not in (None, "") else []


def _provider_delivery_id(
    payload: dict[str, Any] | None, headers: dict[str, str]
) -> str | None:
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    for key in _DELIVERY_ID_HEADERS:
        value = normalized_headers.get(key)
        if value not in (None, ""):
            return str(value)
    if payload is None:
        return None
    for key in ("id", "event_id", "webhook_event_id", "delivery_id"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("event_id", "webhook_event_id", "delivery_id"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _provider_subscription_id(payload: dict[str, Any] | None) -> str | None:
    if payload is None:
        return None
    for key in ("subscription_id", "webhook_subscription_id", "webhook_id"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("subscription_id", "webhook_subscription_id", "webhook_id"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _extract_endpoint_id(raw: dict[str, Any]) -> str | None:
    data = raw.get("data") if isinstance(raw, dict) else None
    if isinstance(data, dict):
        value = data.get("id")
        return str(value) if value not in (None, "") else None
    if isinstance(data, list):
        first = next((item for item in data if isinstance(item, dict)), None)
        if first is not None and first.get("id") not in (None, ""):
            return str(first["id"])
    return None


def _extract_provider_subscription_id(raw: dict[str, Any]) -> str | None:
    data = raw.get("data") if isinstance(raw, dict) else None
    if isinstance(data, dict):
        for key in ("id", "webhook_id", "subscription_id"):
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
        nested = data.get("webhook") or data.get("subscription")
        if isinstance(nested, dict):
            for key in ("id", "webhook_id", "subscription_id"):
                value = nested.get(key)
                if value not in (None, ""):
                    return str(value)
    return None
