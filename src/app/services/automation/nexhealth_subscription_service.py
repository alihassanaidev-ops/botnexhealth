"""NexHealth webhook subscription lifecycle/health service (Plan 09).

The public NexHealth subscription API shape is deliberately isolated here. The
core lifecycle state is local and testable; remote creation is attempted only
when a callback URL is supplied by the caller/deployment.
"""

from __future__ import annotations

import logging
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

# NexHealth's valid appointment webhook events (verified live against the sandbox
# 2026-07-14: only these two are accepted — cancellations/deletions arrive as
# `appointment_updated`, not a distinct event). Subscribe with resource_type="Appointment".
DEFAULT_APPOINTMENT_EVENTS = [
    "appointment_insertion",
    "appointment_updated",
]
_WEBHOOK_RESOURCE_TYPE = "Appointment"


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
            stmt = stmt.where(NexHealthWebhookSubscription.institution_id == institution_id)
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
                InstitutionLocation.nexhealth_subdomain.is_not(None),
                InstitutionLocation.nexhealth_location_id.is_not(None),
            )
        )
        created = 0
        updated = 0
        activated = 0
        failed = 0
        for location, institution in result.all():
            row, was_created = await self.ensure_location_subscription(
                institution=institution,
                location=location,
                callback_url=callback_url,
                event_types=event_types or DEFAULT_APPOINTMENT_EVENTS,
            )
            created += int(was_created)
            updated += int(not was_created)
            activated += int(row.status == NexHealthWebhookSubscriptionStatus.ACTIVE.value)
            failed += int(row.status == NexHealthWebhookSubscriptionStatus.FAILED.value)
        return {
            "created": created,
            "updated": updated,
            "activated": activated,
            "failed": failed,
        }

    async def configured_subscription_targets(self) -> list[tuple[str, str]]:
        """Return (institution_id, location_id) for PMS-configured locations."""
        result = await self.session.execute(
            select(InstitutionLocation).where(
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
            select(NexHealthWebhookSubscription).where(
                NexHealthWebhookSubscription.status.in_(
                    [
                        NexHealthWebhookSubscriptionStatus.ACTIVE.value,
                        NexHealthWebhookSubscriptionStatus.PENDING.value,
                    ]
                )
            )
        )
        return [
            (str(row.institution_id), str(row.id))
            for row in result.scalars().all()
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
        events = event_types or DEFAULT_APPOINTMENT_EVENTS
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
                status=NexHealthWebhookSubscriptionStatus.PENDING.value,
                updated_at=now,
            )
            self.session.add(existing)
            was_created = True
        else:
            existing.subdomain = str(location.nexhealth_subdomain)
            existing.nexhealth_location_id = str(location.nexhealth_location_id)
            existing.event_types = events
            existing.updated_at = now
            if existing.status == NexHealthWebhookSubscriptionStatus.DISABLED.value:
                existing.status = NexHealthWebhookSubscriptionStatus.PENDING.value
            was_created = False

        if callback_url and not existing.provider_subscription_id:
            await self._try_remote_create(
                row=existing,
                institution=institution,
                location=location,
                callback_url=callback_url,
                event_types=events,
            )
        return existing, was_created

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

    async def health_check(self, *, stale_after_hours: int = 24) -> SubscriptionHealthSummary:
        rows = await self.list_subscriptions()
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(hours=stale_after_hours)
        summary = SubscriptionHealthSummary(total=len(rows))
        for row in rows:
            row.last_health_check_at = now
            if row.status == NexHealthWebhookSubscriptionStatus.ACTIVE.value:
                if row.last_event_at is not None and _as_utc(row.last_event_at) < stale_before:
                    row.status = NexHealthWebhookSubscriptionStatus.FAILED.value
                    row.error_metadata = {
                        "reason": "stale_webhook_events",
                        "stale_after_hours": stale_after_hours,
                    }
                    summary.stale_marked += 1
            if hasattr(summary, row.status):
                setattr(summary, row.status, getattr(summary, row.status) + 1)
            row.updated_at = now
        return summary

    async def _try_remote_create(
        self,
        *,
        row: NexHealthWebhookSubscription,
        institution: Institution,
        location: InstitutionLocation,
        callback_url: str,
        event_types: list[str],
    ) -> None:
        """Best-effort provider create.

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
        adapter = None
        secret_key: str | None = None
        try:
            adapter = await NexHealthAdapter.create(institution, location)
            subdomain = adapter._default_params().get("subdomain")  # noqa: SLF001

            endpoint = await handle_nexhealth_request(
                adapter._client,  # noqa: SLF001
                "POST",
                "/webhook_endpoints",
                json={"target_url": callback_url},
            )
            ep_data = endpoint.get("data") if isinstance(endpoint, dict) else None
            endpoint_id = (ep_data or {}).get("id")
            secret_key = (ep_data or {}).get("secret_key")
            if not endpoint_id:
                raise RuntimeError("webhook_endpoint id missing in response")

            for event in event_types:
                await handle_nexhealth_request(
                    adapter._client,  # noqa: SLF001
                    "POST",
                    f"/webhook_endpoints/{endpoint_id}/webhook_subscriptions",
                    params={"subdomain": subdomain},
                    json={"resource_type": _WEBHOOK_RESOURCE_TYPE, "event": event},
                )
        except Exception as exc:  # noqa: BLE001
            row.status = NexHealthWebhookSubscriptionStatus.FAILED.value
            row.error_metadata = {"type": type(exc).__name__}
            logger.warning(
                "nexhealth subscription create failed institution=%s location=%s type=%s",
                institution.id,
                location.id,
                type(exc).__name__,
            )
            return
        finally:
            if adapter is not None:
                await adapter.close()

        row.provider_subscription_id = str(endpoint_id)
        row.status = NexHealthWebhookSubscriptionStatus.ACTIVE.value
        row.error_metadata = None
        # The endpoint's secret_key is the inbound-webhook signing secret. Persist it if
        # the model carries a column (per-endpoint secret; falls back to the platform
        # NEXHEALTH_WEBHOOK_SECRET otherwise — see the inbound verifier).
        if secret_key and hasattr(row, "secret_key"):
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


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
