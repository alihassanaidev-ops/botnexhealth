"""NexHealth webhook subscription lifecycle/health service (Plan 09).

The public NexHealth subscription API shape is deliberately isolated here. The
core lifecycle state is local and testable; remote creation is attempted only
when a callback URL is supplied by the caller/deployment.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.nexhealth_webhook_subscription import (
    NexHealthWebhookSubscription,
    NexHealthWebhookSubscriptionStatus,
)

logger = logging.getLogger(__name__)

# NexHealth's appointment webhook events relevant to scheduled appointments.
# `appointment_created` is the PMS-originated path: NexHealth fires it when it
# detects a new appointment in the health record system. `appointment_insertion`
# is the API-write path and `appointment_updated` carries changes/cancellations.
DEFAULT_APPOINTMENT_EVENTS = [
    "appointment_insertion",
    "appointment_created",
    "appointment_updated",
]
DEFAULT_PATIENT_EVENTS = [
    "patient_created",
    "patient_updated",
]
DEFAULT_SYNC_STATUS_EVENTS = [
    "sync_status_read_change",
    "sync_status_write_change",
]
DEFAULT_WEBHOOK_EVENTS = (
    DEFAULT_APPOINTMENT_EVENTS + DEFAULT_PATIENT_EVENTS + DEFAULT_SYNC_STATUS_EVENTS
)

_EVENT_RESOURCE_TYPES = {
    **{event: "Appointment" for event in DEFAULT_APPOINTMENT_EVENTS},
    **{event: "Patient" for event in DEFAULT_PATIENT_EVENTS},
    **{event: "SyncStatus" for event in DEFAULT_SYNC_STATUS_EVENTS},
}


def nexhealth_live_callback_url(
    *, public_api_url: str | None, explicit_callback_url: str | None
) -> str | None:
    explicit = (explicit_callback_url or "").strip()
    if explicit:
        return explicit
    base = (public_api_url or "").strip().rstrip("/")
    return f"{base}/api/v1/nexhealth/webhooks/appointments" if base else None


@dataclass
class SubscriptionHealthSummary:
    total: int = 0
    active: int = 0
    pending: int = 0
    disabled: int = 0
    failed: int = 0
    stale_marked: int = 0


class NexHealthSubscriptionLifecycleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_subscriptions(
        self, *, institution_id: str | None = None
    ) -> list[NexHealthWebhookSubscription]:
        stmt = select(NexHealthWebhookSubscription)
        if institution_id:
            stmt = stmt.where(
                NexHealthWebhookSubscription.institution_id == institution_id
            )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def ensure_for_configured_locations(
        self,
        *,
        callback_url: str | None = None,
        event_types: list[str] | None = None,
    ) -> dict[str, int]:
        """Ensure a local subscription row exists for every PMS-configured location.

        When ``callback_url`` is supplied, the service attempts a remote NexHealth
        subscription create for rows without ``provider_subscription_id``. Without
        it, rows remain ``pending`` and still participate in health/backfill ops.
        """
        result = await self.session.execute(
            select(InstitutionLocation, Institution)
            .join(Institution, Institution.id == InstitutionLocation.institution_id)
            .where(
                Institution.pms_type == "nexhealth",
                InstitutionLocation.nexhealth_subdomain.is_not(None),
                InstitutionLocation.nexhealth_location_id.is_not(None),
            )
        )
        pairs = list(result.all())
        created = 0
        updated = 0
        activated = 0
        failed = 0
        grouped: dict[
            tuple[str, str, str], list[tuple[Any, Institution, InstitutionLocation]]
        ] = defaultdict(list)
        for location, institution in pairs:
            row, was_created = await self.ensure_location_subscription(
                institution=institution,
                location=location,
                callback_url=None,
                event_types=event_types or DEFAULT_WEBHOOK_EVENTS,
            )
            credential_hash = row.api_key_hash or row.credential_mode or "unknown"
            grouped[
                (
                    str(institution.id),
                    str(location.nexhealth_subdomain),
                    credential_hash,
                )
            ].append((row, institution, location))
            created += int(was_created)
            updated += int(not was_created)

        if callback_url:
            for members in grouped.values():
                rows = [item[0] for item in members]
                await self._ensure_remote_group(
                    rows=rows,
                    institution=members[0][1],
                    location=members[0][2],
                    callback_url=callback_url,
                    event_types=event_types or DEFAULT_WEBHOOK_EVENTS,
                )

        for members in grouped.values():
            for row, _, _ in members:
                activated += int(
                    row.status == NexHealthWebhookSubscriptionStatus.ACTIVE.value
                )
                failed += int(
                    row.status == NexHealthWebhookSubscriptionStatus.FAILED.value
                )
        return {
            "created": created,
            "updated": updated,
            "activated": activated,
            "failed": failed,
        }

    async def configured_subscription_targets(self) -> list[tuple[str, str]]:
        """Return (institution_id, location_id) for PMS-configured locations."""
        result = await self.session.execute(
            select(InstitutionLocation)
            .join(Institution, Institution.id == InstitutionLocation.institution_id)
            .where(
                Institution.pms_type == "nexhealth",
                InstitutionLocation.nexhealth_subdomain.is_not(None),
                InstitutionLocation.nexhealth_location_id.is_not(None),
            )
        )
        return [
            (str(location.institution_id), str(location.id))
            for location in result.scalars().all()
        ]

    async def active_or_pending_targets(self) -> list[tuple[str, str]]:
        """Return (institution_id, subscription_id) for rows due for sync."""
        result = await self.session.execute(
            select(NexHealthWebhookSubscription)
            .join(
                Institution,
                Institution.id == NexHealthWebhookSubscription.institution_id,
            )
            .where(
                Institution.pms_type == "nexhealth",
                NexHealthWebhookSubscription.status.in_(
                    [
                        NexHealthWebhookSubscriptionStatus.ACTIVE.value,
                        NexHealthWebhookSubscriptionStatus.PENDING.value,
                    ]
                ),
            )
        )
        return [
            (str(row.institution_id), str(row.id)) for row in result.scalars().all()
        ]

    async def ensure_location_subscription(
        self,
        *,
        institution: Institution,
        location: InstitutionLocation,
        callback_url: str | None = None,
        event_types: list[str] | None = None,
    ) -> tuple[NexHealthWebhookSubscription, bool]:
        institution_id = str(institution.id)
        location_id = str(location.id)
        events = event_types or DEFAULT_WEBHOOK_EVENTS
        from src.app.dependencies import resolve_nexhealth_credential

        credential = resolve_nexhealth_credential(institution)
        existing = (
            await self.session.execute(
                select(NexHealthWebhookSubscription).where(
                    NexHealthWebhookSubscription.institution_id == institution_id,
                    NexHealthWebhookSubscription.location_id == location_id,
                )
            )
        ).scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if existing is None:
            existing = NexHealthWebhookSubscription(
                id=str(uuid4()),
                institution_id=institution_id,
                location_id=location_id,
                subdomain=str(location.nexhealth_subdomain),
                nexhealth_location_id=str(location.nexhealth_location_id),
                event_types=events,
                credential_mode=credential.mode,
                api_key_hash=credential.api_key_hash,
                status=NexHealthWebhookSubscriptionStatus.PENDING.value,
                updated_at=now,
            )
            self.session.add(existing)
            was_created = True
        else:
            existing.subdomain = str(location.nexhealth_subdomain)
            existing.nexhealth_location_id = str(location.nexhealth_location_id)
            existing.event_types = events
            existing.credential_mode = credential.mode
            existing.api_key_hash = credential.api_key_hash
            existing.updated_at = now
            if existing.status == NexHealthWebhookSubscriptionStatus.DISABLED.value:
                existing.status = NexHealthWebhookSubscriptionStatus.PENDING.value
            was_created = False

        if callback_url:
            await self._ensure_remote_group(
                rows=[existing],
                institution=institution,
                location=location,
                callback_url=callback_url,
                event_types=events,
            )
        return existing, was_created

    async def ensure_for_institution(
        self,
        *,
        institution: Institution,
        callback_url: str,
        event_types: list[str] | None = None,
    ) -> list[NexHealthWebhookSubscription]:
        """Create/repair one endpoint subscription set per NexHealth subdomain.

        Sync watermarks remain location-scoped, but provider webhook subscriptions
        are subdomain-scoped. Rows for sibling locations intentionally receive the
        same endpoint, event-subscription ids, callback and signing secret.
        """
        result = await self.session.execute(
            select(InstitutionLocation).where(
                InstitutionLocation.institution_id == str(institution.id),
                InstitutionLocation.nexhealth_subdomain.is_not(None),
                InstitutionLocation.nexhealth_location_id.is_not(None),
            )
        )
        locations = list(result.scalars().all())
        if not locations:
            return []

        events = event_types or DEFAULT_WEBHOOK_EVENTS
        groups: dict[
            str, list[tuple[NexHealthWebhookSubscription, InstitutionLocation]]
        ] = defaultdict(list)
        for location in locations:
            row, _ = await self.ensure_location_subscription(
                institution=institution,
                location=location,
                callback_url=None,
                event_types=events,
            )
            groups[str(location.nexhealth_subdomain)].append((row, location))

        rows: list[NexHealthWebhookSubscription] = []
        for members in groups.values():
            group_rows = [item[0] for item in members]
            await self._ensure_remote_group(
                rows=group_rows,
                institution=institution,
                location=members[0][1],
                callback_url=callback_url,
                event_types=events,
            )
            rows.extend(group_rows)
        return rows

    async def verify_for_institution(
        self,
        *,
        institution: Institution,
        expected_callback_url: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read provider state and refresh local health without creating anything."""
        from src.app.api.helpers import handle_nexhealth_request
        from src.app.pms.nexhealth.adapter import NexHealthAdapter

        result = await self.session.execute(
            select(NexHealthWebhookSubscription, InstitutionLocation)
            .join(
                InstitutionLocation,
                InstitutionLocation.id == NexHealthWebhookSubscription.location_id,
            )
            .where(NexHealthWebhookSubscription.institution_id == str(institution.id))
        )
        groups: dict[
            str, list[tuple[NexHealthWebhookSubscription, InstitutionLocation]]
        ] = defaultdict(list)
        for row, location in result.all():
            groups[row.subdomain].append((row, location))

        verified: list[dict[str, Any]] = []
        for subdomain, members in groups.items():
            controller = next(
                (row for row, _ in members if row.provider_subscription_id),
                members[0][0],
            )
            endpoint_id = controller.provider_subscription_id
            active_events: set[str] = set()
            provider_callback: str | None = None
            error_type: str | None = None
            adapter = None
            try:
                if not endpoint_id:
                    raise RuntimeError("provider endpoint is not configured")
                adapter = await NexHealthAdapter.create(institution, members[0][1])
                endpoints_response = await handle_nexhealth_request(
                    adapter._client,
                    "GET",
                    "/webhook_endpoints",  # noqa: SLF001
                )
                endpoints_data = (
                    endpoints_response.get("data", [])
                    if isinstance(endpoints_response, dict)
                    else []
                )
                if isinstance(endpoints_data, dict):
                    endpoints_data = [endpoints_data]
                endpoint = next(
                    (
                        item
                        for item in endpoints_data
                        if isinstance(item, dict)
                        and str(item.get("id")) == str(endpoint_id)
                    ),
                    None,
                )
                if endpoint is None or endpoint.get("active") is False:
                    raise RuntimeError("provider endpoint is missing or inactive")
                provider_callback = _clean_optional_str(endpoint.get("target_url"))

                subscriptions_response = await handle_nexhealth_request(
                    adapter._client,  # noqa: SLF001
                    "GET",
                    f"/webhook_endpoints/{endpoint_id}/webhook_subscriptions",
                    params={"subdomain": subdomain},
                )
                subscriptions_data = (
                    subscriptions_response.get("data", [])
                    if isinstance(subscriptions_response, dict)
                    else []
                )
                if isinstance(subscriptions_data, dict):
                    subscriptions_data = [subscriptions_data]
                active_events = {
                    str(item.get("event") or item.get("event_name"))
                    for item in subscriptions_data
                    if isinstance(item, dict) and item.get("active") is not False
                }
            except Exception as exc:  # noqa: BLE001
                error_type = type(exc).__name__
            finally:
                if adapter is not None:
                    await adapter.close()

            missing_events = sorted(set(DEFAULT_WEBHOOK_EVENTS) - active_events)
            callback_matches = bool(
                provider_callback
                and (expected_callback_url or controller.callback_url)
                and provider_callback.rstrip("/")
                == str(expected_callback_url or controller.callback_url).rstrip("/")
            )
            healthy = error_type is None and not missing_events and callback_matches
            now = datetime.now(timezone.utc)
            for row, _ in members:
                row.last_health_check_at = now
                row.updated_at = now
                row.status = (
                    NexHealthWebhookSubscriptionStatus.ACTIVE.value
                    if healthy
                    else NexHealthWebhookSubscriptionStatus.FAILED.value
                )
                row.error_metadata = (
                    None
                    if healthy
                    else {
                        "reason": "provider_verification_failed",
                        "type": error_type,
                        "missing_events": missing_events,
                        "callback_matches": callback_matches,
                    }
                )
            verified.append(
                {
                    "subdomain": subdomain,
                    "healthy": healthy,
                    "endpoint_id": str(endpoint_id) if endpoint_id else None,
                    "callback_url": provider_callback,
                    "active_events": sorted(active_events),
                    "missing_events": missing_events,
                    "callback_matches": callback_matches,
                    "error_type": error_type,
                }
            )
        return verified

    async def record_event_seen(self, *, institution_id: str, location_id: str) -> None:
        row = (
            await self.session.execute(
                select(NexHealthWebhookSubscription).where(
                    NexHealthWebhookSubscription.institution_id == institution_id,
                    NexHealthWebhookSubscription.location_id == location_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return
        now = datetime.now(timezone.utc)
        row.last_event_at = now
        row.last_health_check_at = now
        row.updated_at = now
        if row.provider_subscription_id:
            row.status = NexHealthWebhookSubscriptionStatus.ACTIVE.value

    async def health_check(
        self, *, stale_after_hours: int = 24
    ) -> SubscriptionHealthSummary:
        rows = await self.list_subscriptions()
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(hours=stale_after_hours)
        summary = SubscriptionHealthSummary(total=len(rows))
        for row in rows:
            row.last_health_check_at = now
            if row.status == NexHealthWebhookSubscriptionStatus.ACTIVE.value:
                if (
                    row.last_event_at is not None
                    and _as_utc(row.last_event_at) < stale_before
                ):
                    row.status = NexHealthWebhookSubscriptionStatus.FAILED.value
                    row.error_metadata = {
                        "reason": "stale_webhook_events",
                        "stale_after_hours": stale_after_hours,
                    }
                    summary.stale_marked += 1
                elif row.last_event_at is None:
                    # A successful provider repair refreshes updated_at. Use it
                    # before created_at so an older local routing row is not
                    # immediately failed while waiting for its first delivery.
                    reference_at = (
                        getattr(row, "updated_at", None)
                        or getattr(row, "created_at", None)
                        or row.last_health_check_at
                    )
                    if (
                        reference_at is not None
                        and _as_utc(reference_at) < stale_before
                    ):
                        row.status = NexHealthWebhookSubscriptionStatus.FAILED.value
                        row.error_metadata = {
                            "reason": "no_webhook_events_seen",
                            "stale_after_hours": stale_after_hours,
                        }
                        summary.stale_marked += 1
            if hasattr(summary, row.status):
                setattr(summary, row.status, getattr(summary, row.status) + 1)
            row.updated_at = now
        return summary

    async def _ensure_remote_group(
        self,
        *,
        rows: list[NexHealthWebhookSubscription],
        institution: Institution,
        location: InstitutionLocation,
        callback_url: str,
        event_types: list[str],
    ) -> None:
        """Best-effort create/repair for one credential + NexHealth subdomain.

        The endpoint is isolated here because NexHealth account capabilities vary
        by partner setup. If the call fails, local lifecycle state remains useful:
        operators see ``failed`` and can retry after confirming vendor config.
        """
        from src.app.api.helpers import handle_nexhealth_request
        from src.app.pms.nexhealth.adapter import NexHealthAdapter

        # NexHealth v2 webhook registration is a TWO-step, account-level flow (verified
        # live 2026-07-14). The legacy single `POST /webhooks` endpoint we used before is
        # gone (404); it was a pre-v2.2.2 shape:
        #   1) POST /webhook_endpoints  {"target_url": ...}  -> {id, secret_key}
        #   2) POST /webhook_endpoints/{id}/webhook_subscriptions?subdomain=X
        #        {"resource_type": "Appointment", "event": <event>}   per event
        # The endpoint is account-level (subdomain ignored on create); subscriptions are
        # subdomain-scoped. The returned secret_key is the inbound signing secret.
        controller = next(
            (item for item in rows if item.provider_subscription_id), rows[0]
        )
        adapter = None
        secret_key: str | None = None
        endpoint_id: str | int | None = controller.provider_subscription_id
        provider_subscription_ids: list[str] = []
        try:
            adapter = await NexHealthAdapter.create(institution, location)
            subdomain = adapter._default_params().get("subdomain")  # noqa: SLF001

            # Endpoints belong to the authenticated API account, while event
            # subscriptions below belong to a subdomain. Platform credentials
            # can therefore serve multiple institutions/subdomains through one
            # endpoint. Reuse our managed endpoint instead of creating duplicate
            # account-level callbacks for every tenant.
            if not endpoint_id or not controller.secret_key:
                managed = (
                    await self.session.execute(
                        select(NexHealthWebhookSubscription)
                        .where(
                            NexHealthWebhookSubscription.api_key_hash
                            == adapter.api_key_hash,
                            NexHealthWebhookSubscription.provider_subscription_id.is_not(
                                None
                            ),
                            NexHealthWebhookSubscription.secret_key_encrypted.is_not(
                                None
                            ),
                            NexHealthWebhookSubscription.status
                            != NexHealthWebhookSubscriptionStatus.DISABLED.value,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if managed is not None:
                    endpoint_id = managed.provider_subscription_id
                    secret_key = managed.secret_key

            if endpoint_id and (secret_key or controller.secret_key):
                await handle_nexhealth_request(
                    adapter._client,  # noqa: SLF001
                    "PATCH",
                    f"/webhook_endpoints/{endpoint_id}",
                    json={"target_url": callback_url, "active": True},
                )
                secret_key = secret_key or controller.secret_key
            else:
                # A signing secret is returned on endpoint creation. Never mark a
                # connection active if we cannot authenticate its deliveries.
                endpoint = await handle_nexhealth_request(
                    adapter._client,  # noqa: SLF001
                    "POST",
                    "/webhook_endpoints",
                    json={"target_url": callback_url, "active": True},
                )
                ep_data = endpoint.get("data") if isinstance(endpoint, dict) else None
                if isinstance(ep_data, list):
                    ep_data = ep_data[0] if ep_data else None
                endpoint_id = (ep_data or {}).get("id")
                secret_key = (ep_data or {}).get("secret_key")
                if not endpoint_id or not secret_key:
                    raise RuntimeError("webhook endpoint id or signing secret missing")

            existing_response = await handle_nexhealth_request(
                adapter._client,  # noqa: SLF001
                "GET",
                f"/webhook_endpoints/{endpoint_id}/webhook_subscriptions",
                params={"subdomain": subdomain},
            )
            existing_data = (
                existing_response.get("data", [])
                if isinstance(existing_response, dict)
                else []
            )
            if isinstance(existing_data, dict):
                existing_data = [existing_data]
            existing_by_event = {
                str(item.get("event") or item.get("event_name")): item
                for item in existing_data
                if isinstance(item, dict)
            }

            for event in event_types:
                item = existing_by_event.get(event)
                if item is None:
                    created = await handle_nexhealth_request(
                        adapter._client,  # noqa: SLF001
                        "POST",
                        f"/webhook_endpoints/{endpoint_id}/webhook_subscriptions",
                        params={"subdomain": subdomain},
                        json={
                            "resource_type": _resource_type_for_event(event),
                            "event": event,
                            "active": True,
                        },
                    )
                    created_data = (
                        created.get("data") if isinstance(created, dict) else None
                    )
                    if isinstance(created_data, list):
                        created_data = created_data[0] if created_data else None
                    item = created_data if isinstance(created_data, dict) else {}
                elif item.get("active") is False and item.get("id") is not None:
                    await handle_nexhealth_request(
                        adapter._client,  # noqa: SLF001
                        "PATCH",
                        f"/webhook_endpoints/{endpoint_id}/webhook_subscriptions/{item['id']}",
                        params={"subdomain": subdomain},
                        json={"active": True},
                    )
                if item.get("id") is not None:
                    provider_subscription_ids.append(str(item["id"]))
            if len(provider_subscription_ids) != len(event_types):
                raise RuntimeError("one or more webhook subscription ids are missing")
        except Exception as exc:  # noqa: BLE001
            for row in rows:
                row.status = NexHealthWebhookSubscriptionStatus.FAILED.value
                row.error_metadata = {"type": type(exc).__name__}
            logger.warning(
                "nexhealth subscription setup failed institution=%s subdomain=%s type=%s",
                institution.id,
                location.nexhealth_subdomain,
                type(exc).__name__,
            )
            return
        finally:
            if adapter is not None:
                await adapter.close()

        now = datetime.now(timezone.utc)
        for row in rows:
            row.provider_subscription_id = str(endpoint_id)
            row.provider_subscription_ids = provider_subscription_ids
            row.callback_url = callback_url
            row.credential_mode = adapter.credential_mode
            row.api_key_hash = adapter.api_key_hash
            row.status = NexHealthWebhookSubscriptionStatus.ACTIVE.value
            row.error_metadata = None
            row.last_health_check_at = now
            row.updated_at = now
            row.secret_key = secret_key


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


def _resource_type_for_event(event: str) -> str:
    base = event.split(".", 1)[0]
    try:
        return _EVENT_RESOURCE_TYPES[base]
    except KeyError as exc:
        raise ValueError(f"Unsupported NexHealth webhook event: {event}") from exc


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _clean_optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    cleaned = str(value).strip()
    return cleaned or None


async def live_signature_secrets(session: AsyncSession) -> list[str]:
    """Return the distinct live endpoint secrets accepted by the receiver."""
    result = await session.execute(
        select(NexHealthWebhookSubscription).where(
            NexHealthWebhookSubscription.status
            != NexHealthWebhookSubscriptionStatus.DISABLED.value,
            NexHealthWebhookSubscription.secret_key_encrypted.is_not(None),
        )
    )
    secrets: list[str] = []
    seen: set[str] = set()
    for row in result.scalars().all():
        secret = row.secret_key
        if secret and secret not in seen:
            secrets.append(secret)
            seen.add(secret)
    return secrets
