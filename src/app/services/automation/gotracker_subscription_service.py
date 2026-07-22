"""GoTracker webhook subscription lifecycle service."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import settings
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

    async def _try_remote_create(
        self,
        *,
        row: GoTrackerWebhookSubscription,
        institution: Institution,
        location: InstitutionLocation,
        callback_url: str,
        event_types: list[str],
    ) -> None:
        from src.app.pms.gotracker.adapter import GoTrackerAdapter

        if not settings.gotracker_webhook_secret:
            row.status = GoTrackerWebhookSubscriptionStatus.FAILED.value
            row.error_metadata = {"reason": "missing_gotracker_webhook_secret"}
            return

        adapter = None
        try:
            adapter = await GoTrackerAdapter.create(institution, location)
            provider_ids: list[str] = []
            for event_type in event_types:
                raw = await adapter._client.request(  # noqa: SLF001
                    "POST",
                    "/api/webhooks/subscriptions",
                    json={
                        "url": callback_url,
                        "event_types": event_type,
                        "secret": settings.gotracker_webhook_secret,
                    },
                )
                provider_id = _extract_provider_subscription_id(raw)
                if provider_id:
                    provider_ids.append(provider_id)
        except Exception as exc:  # noqa: BLE001
            row.status = GoTrackerWebhookSubscriptionStatus.FAILED.value
            row.error_metadata = {"type": type(exc).__name__, "mode": "remote_create"}
            logger.warning(
                "gotracker subscription create failed institution=%s location=%s type=%s",
                institution.id,
                location.id,
                type(exc).__name__,
            )
            return
        finally:
            if adapter is not None:
                await adapter.close()

        row.provider_subscription_id = ",".join(provider_ids) or None
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


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
