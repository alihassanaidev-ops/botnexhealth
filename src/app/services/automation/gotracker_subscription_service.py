"""GoTracker webhook subscription lifecycle service."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.gotracker_webhook_subscription import (
    GoTrackerWebhookSubscription,
    GoTrackerWebhookSubscriptionStatus,
)
from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation

logger = logging.getLogger(__name__)

DEFAULT_GOTRACKER_WEBHOOK_EVENTS = [
    "appointment.created",
    "appointment.updated",
    "appointment.cancelled",
    "appointment.status_writeback.complete",
    "appointment.status_writeback.failed",
    "patient.created",
    "patient.updated",
]


@dataclass
class GoTrackerSubscriptionHealthSummary:
    total: int = 0
    active: int = 0
    pending: int = 0
    disabled: int = 0
    failed: int = 0
    stale_marked: int = 0


class GoTrackerSubscriptionLifecycleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_subscriptions(
        self, *, institution_id: str | None = None
    ) -> list[GoTrackerWebhookSubscription]:
        stmt = select(GoTrackerWebhookSubscription)
        if institution_id:
            stmt = stmt.where(GoTrackerWebhookSubscription.institution_id == institution_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def ensure_for_configured_locations(
        self,
        *,
        callback_base_url: str | None = None,
        event_types: list[str] | None = None,
    ) -> dict[str, int]:
        result = await self.session.execute(
            select(InstitutionLocation, Institution)
            .join(Institution, Institution.id == InstitutionLocation.institution_id)
            .where(
                Institution.pms_type == "gotracker",
                InstitutionLocation.gotracker_product_key_encrypted.is_not(None),
            )
        )
        created = 0
        updated = 0
        activated = 0
        failed = 0
        for location, institution in result.all():
            callback_url = (
                _location_callback_url(callback_base_url, str(location.id))
                if callback_base_url
                else None
            )
            row, was_created = await self.ensure_location_subscription(
                institution=institution,
                location=location,
                callback_url=callback_url,
                event_types=event_types or DEFAULT_GOTRACKER_WEBHOOK_EVENTS,
            )
            created += int(was_created)
            updated += int(not was_created)
            activated += int(row.status == GoTrackerWebhookSubscriptionStatus.ACTIVE.value)
            failed += int(row.status == GoTrackerWebhookSubscriptionStatus.FAILED.value)
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
        callback_url: str | None = None,
        event_types: list[str] | None = None,
    ) -> tuple[GoTrackerWebhookSubscription, bool]:
        institution_id = str(institution.id)
        location_id = str(location.id)
        events = event_types or DEFAULT_GOTRACKER_WEBHOOK_EVENTS
        existing = (
            await self.session.execute(
                select(GoTrackerWebhookSubscription).where(
                    GoTrackerWebhookSubscription.institution_id == institution_id,
                    GoTrackerWebhookSubscription.location_id == location_id,
                )
            )
        ).scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if existing is None:
            existing = GoTrackerWebhookSubscription(
                id=str(uuid4()),
                institution_id=institution_id,
                location_id=location_id,
                callback_url=callback_url,
                event_types=events,
                status=GoTrackerWebhookSubscriptionStatus.PENDING.value,
                updated_at=now,
            )
            self.session.add(existing)
            was_created = True
        else:
            existing.callback_url = callback_url or existing.callback_url
            existing.event_types = events
            existing.updated_at = now
            if existing.status == GoTrackerWebhookSubscriptionStatus.DISABLED.value:
                existing.status = GoTrackerWebhookSubscriptionStatus.PENDING.value
            was_created = False

        if callback_url:
            await self._try_remote_reconcile(
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
                select(GoTrackerWebhookSubscription).where(
                    GoTrackerWebhookSubscription.institution_id == institution_id,
                    GoTrackerWebhookSubscription.location_id == location_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return
        now = datetime.now(timezone.utc)
        row.last_event_at = now
        row.last_health_check_at = now
        row.updated_at = now
        if row.status != GoTrackerWebhookSubscriptionStatus.DISABLED.value:
            row.status = GoTrackerWebhookSubscriptionStatus.ACTIVE.value

    async def health_check(self, *, stale_after_hours: int = 24) -> GoTrackerSubscriptionHealthSummary:
        rows = await self.list_subscriptions()
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(hours=stale_after_hours)
        summary = GoTrackerSubscriptionHealthSummary(total=len(rows))
        for row in rows:
            row.last_health_check_at = now
            if row.status == GoTrackerWebhookSubscriptionStatus.ACTIVE.value:
                if row.last_event_at is not None and _as_utc(row.last_event_at) < stale_before:
                    row.status = GoTrackerWebhookSubscriptionStatus.FAILED.value
                    row.error_metadata = {
                        "reason": "stale_webhook_events",
                        "stale_after_hours": stale_after_hours,
                    }
                    summary.stale_marked += 1
                elif row.last_event_at is None:
                    reference_at = (
                        getattr(row, "created_at", None)
                        or getattr(row, "updated_at", None)
                        or row.last_health_check_at
                    )
                    if reference_at is not None and _as_utc(reference_at) < stale_before:
                        row.status = GoTrackerWebhookSubscriptionStatus.FAILED.value
                        row.error_metadata = {
                            "reason": "no_webhook_events_seen",
                            "stale_after_hours": stale_after_hours,
                        }
                        summary.stale_marked += 1
            if hasattr(summary, row.status):
                setattr(summary, row.status, getattr(summary, row.status) + 1)
            row.updated_at = now
        return summary

    async def _try_remote_reconcile(
        self,
        *,
        row: GoTrackerWebhookSubscription,
        institution: Institution,
        location: InstitutionLocation,
        callback_url: str,
        event_types: list[str],
    ) -> None:
        from src.app.pms.gotracker.adapter import GoTrackerAdapter

        adapter = None
        try:
            adapter = await GoTrackerAdapter.create(institution, location)
            subscriptions = await _list_remote_subscriptions(adapter)
            matches = _matching_remote_subscriptions(
                subscriptions,
                callback_url=callback_url,
                provider_subscription_id=row.provider_subscription_id,
            )
            if matches:
                primary = matches[0]
                provider_id = _extract_provider_subscription_id(primary)
                if provider_id:
                    await adapter._client.request(  # noqa: SLF001
                        "PATCH",
                        f"/api/webhooks/subscriptions/{provider_id}",
                        json=_subscription_payload(
                            callback_url=callback_url,
                            event_types=event_types,
                            secret=getattr(location, "gotracker_webhook_secret", None),
                            include_secret=False,
                        ),
                    )
                    row.provider_subscription_id = provider_id
                    await _delete_duplicate_remote_subscriptions(adapter, matches[1:])
            elif row.provider_subscription_id:
                provider_id = row.provider_subscription_id.split(",", 1)[0].strip()
                if provider_id:
                    await adapter._client.request(  # noqa: SLF001
                        "PATCH",
                        f"/api/webhooks/subscriptions/{provider_id}",
                        json=_subscription_payload(
                            callback_url=callback_url,
                            event_types=event_types,
                            secret=getattr(location, "gotracker_webhook_secret", None),
                            include_secret=False,
                        ),
                    )
                    row.provider_subscription_id = provider_id
            else:
                raw = await adapter._client.request(  # noqa: SLF001
                    "POST",
                    "/api/webhooks/subscriptions",
                    json=_subscription_payload(
                        callback_url=callback_url,
                        event_types=event_types,
                        secret=getattr(location, "gotracker_webhook_secret", None),
                        include_secret=True,
                    ),
                )
                provider_id = _extract_provider_subscription_id(raw)
                if provider_id:
                    row.provider_subscription_id = provider_id
                returned_secret = _extract_webhook_secret(raw)
                if returned_secret:
                    location.gotracker_webhook_secret = returned_secret
        except Exception as exc:  # noqa: BLE001
            row.status = GoTrackerWebhookSubscriptionStatus.FAILED.value
            row.error_metadata = {"type": type(exc).__name__, "mode": "remote_reconcile"}
            logger.warning(
                "gotracker subscription reconcile failed institution=%s location=%s type=%s",
                institution.id,
                location.id,
                type(exc).__name__,
            )
            return
        finally:
            if adapter is not None:
                await adapter.close()

        if not row.provider_subscription_id:
            row.status = GoTrackerWebhookSubscriptionStatus.FAILED.value
            row.error_metadata = {"reason": "missing_provider_subscription_id"}
            return
        row.status = GoTrackerWebhookSubscriptionStatus.ACTIVE.value
        row.error_metadata = None


def _location_callback_url(callback_base_url: str | None, location_id: str) -> str:
    if not callback_base_url:
        raise ValueError("callback_base_url is required")
    return f"{callback_base_url.rstrip('/')}/api/v1/gotracker/webhooks/{location_id}"


def _extract_provider_subscription_id(raw: dict[str, Any]) -> str | None:
    data = raw.get("data") if isinstance(raw, dict) else None
    candidates: list[Any] = [raw]
    if isinstance(data, dict):
        candidates.append(data)
        subscription = data.get("subscription") or data.get("webhook")
        if isinstance(subscription, dict):
            candidates.append(subscription)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("id", "subscription_id", "webhook_id"):
            value = candidate.get(key)
            if value not in (None, ""):
                return str(value)
    return None


async def _list_remote_subscriptions(adapter: Any) -> list[dict[str, Any]]:
    try:
        raw = await adapter._client.request("GET", "/api/webhooks/subscriptions")  # noqa: SLF001
    except Exception:
        return []
    return _subscription_items(raw)


def _subscription_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    data = raw.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "subscriptions", "webhooks"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    for key in ("items", "subscriptions", "webhooks"):
        value = raw.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _matching_remote_subscriptions(
    subscriptions: list[dict[str, Any]],
    *,
    callback_url: str,
    provider_subscription_id: str | None,
) -> list[dict[str, Any]]:
    provider_ids = {
        item.strip()
        for item in (provider_subscription_id or "").split(",")
        if item.strip()
    }
    matches: list[dict[str, Any]] = []
    for subscription in subscriptions:
        subscription_id = _extract_provider_subscription_id(subscription)
        url = _subscription_url(subscription)
        if (subscription_id and subscription_id in provider_ids) or url == callback_url:
            matches.append(subscription)
    return matches


def _subscription_url(subscription: dict[str, Any]) -> str | None:
    for key in ("url", "callback_url", "endpoint_url", "webhook_url"):
        value = subscription.get(key)
        if value not in (None, ""):
            return str(value)
    data = subscription.get("data")
    if isinstance(data, dict):
        return _subscription_url(data)
    return None


def _subscription_payload(
    *,
    callback_url: str,
    event_types: list[str],
    secret: str | None,
    include_secret: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "url": callback_url,
        "event_types": ",".join(event_types),
        "is_active": True,
    }
    if include_secret and secret:
        payload["secret"] = secret
    return payload


def _extract_webhook_secret(raw: dict[str, Any]) -> str | None:
    data = raw.get("data") if isinstance(raw, dict) else None
    candidates: list[Any] = [raw]
    if isinstance(data, dict):
        candidates.append(data)
        subscription = data.get("subscription") or data.get("webhook")
        if isinstance(subscription, dict):
            candidates.append(subscription)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("secret", "webhook_secret", "signing_secret"):
            value = candidate.get(key)
            if value not in (None, ""):
                return str(value)
    return None


async def _delete_duplicate_remote_subscriptions(
    adapter: Any, duplicates: list[dict[str, Any]]
) -> None:
    for duplicate in duplicates:
        provider_id = _extract_provider_subscription_id(duplicate)
        if not provider_id:
            continue
        try:
            await adapter._client.request(  # noqa: SLF001
                "DELETE", f"/api/webhooks/subscriptions/{provider_id}"
            )
        except Exception:
            logger.warning("gotracker duplicate subscription delete failed id=%s", provider_id)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
