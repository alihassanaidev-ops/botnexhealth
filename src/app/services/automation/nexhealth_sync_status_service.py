"""NexHealth PMS sync-status ingestion and polling."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.nexhealth_sync_status import NexHealthSyncStatus
from src.app.models.nexhealth_webhook_subscription import (
    NexHealthWebhookSubscription,
    NexHealthWebhookSubscriptionStatus,
)
from src.app.nexhealth.rate_limit import nexhealth_background_traffic
from src.app.services.location_timezone import (
    extract_timezone,
    is_timezone_unset,
    learn_location_timezone,
)

logger = logging.getLogger(__name__)

HEALTHY_SYNC_STATUSES = frozenset(
    {"green", "ok", "healthy", "connected", "active", "success"}
)
UNHEALTHY_SYNC_STATUSES = frozenset(
    {"red", "down", "error", "failed", "disconnected", "inactive"}
)
SYNC_STATUS_STALE_AFTER = timedelta(hours=24)

#: How often a location's timezone is re-read from NexHealth. Background PMS
#: traffic shares 60 requests/minute per API key with reconciliation and
#: backfill, so this cannot ride the 15-minute sweep it hangs off.
TIMEZONE_CHECK_INTERVAL = timedelta(hours=24)

_LOCATION_PACING_MIN_SECONDS = 0.15
_LOCATION_PACING_MAX_SECONDS = 0.75


@dataclass(frozen=True)
class SyncStatusAssessment:
    read_healthy: bool | None
    write_healthy: bool | None
    stale: bool


@dataclass
class SyncStatusSummary:
    locations_checked: int = 0
    updated: int = 0
    failed_locations: int = 0
    read_unhealthy: int = 0
    write_unhealthy: int = 0
    timezones_learned: int = 0


class NexHealthSyncStatusService:
    """Maintains latest read/write PMS sync health per configured location."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_for_locations(
        self,
        *,
        event: str | None,
        subdomain: str,
        locations: list[InstitutionLocation],
        payload: dict[str, Any],
        checked_at: datetime | None = None,
    ) -> int:
        if not locations:
            return 0
        now = checked_at or datetime.now(timezone.utc)
        status_payload = _sync_status_payload(payload)
        updated = 0
        for location in locations:
            row = (
                await self.session.execute(
                    select(NexHealthSyncStatus).where(
                        NexHealthSyncStatus.institution_id
                        == str(location.institution_id),
                        NexHealthSyncStatus.location_id == str(location.id),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = NexHealthSyncStatus(
                    id=str(uuid4()),
                    institution_id=str(location.institution_id),
                    location_id=str(location.id),
                    subdomain=subdomain,
                    nexhealth_location_id=str(location.nexhealth_location_id),
                )
                self.session.add(row)

            row.subdomain = subdomain
            row.nexhealth_location_id = str(location.nexhealth_location_id)
            row.sync_source_type = _clean_str(status_payload.get("sync_source_type"))
            row.sync_source_name = _clean_str(status_payload.get("sync_source_name"))
            row.emr_payload = (
                status_payload.get("emr")
                if isinstance(status_payload.get("emr"), dict)
                else None
            )
            row.locations_payload = _locations_payload(status_payload)
            row.read_status = _clean_str(status_payload.get("read_status"))
            row.read_status_at = _parse_dt(status_payload.get("read_status_at"))
            row.write_status = _clean_str(status_payload.get("write_status"))
            row.write_status_at = _parse_dt(status_payload.get("write_status_at"))
            row.last_event = event
            row.last_checked_at = now
            row.updated_at = now
            updated += 1
        return updated

    async def resolve_locations_for_payload(
        self, *, subdomain: str, payload: dict[str, Any]
    ) -> list[InstitutionLocation]:
        status_payload = _sync_status_payload(payload)
        location_ids = _nexhealth_location_ids(status_payload)
        stmt = (
            select(InstitutionLocation)
            .join(Institution, Institution.id == InstitutionLocation.institution_id)
            .where(
                Institution.pms_type == "nexhealth",
                InstitutionLocation.nexhealth_subdomain == subdomain,
                InstitutionLocation.nexhealth_location_id.is_not(None),
            )
        )
        if location_ids:
            stmt = stmt.where(
                InstitutionLocation.nexhealth_location_id.in_(location_ids)
            )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest_for_location(
        self, *, institution_id: str, location_id: str
    ) -> NexHealthSyncStatus | None:
        result = await self.session.execute(
            select(NexHealthSyncStatus)
            .where(
                NexHealthSyncStatus.institution_id == institution_id,
                NexHealthSyncStatus.location_id == location_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def poll_all_configured_locations(self) -> SyncStatusSummary:
        rows = await self._load_subscription_locations()
        summary = SyncStatusSummary()
        for idx, row in enumerate(rows):
            if idx > 0:
                await asyncio.sleep(
                    random.uniform(
                        _LOCATION_PACING_MIN_SECONDS, _LOCATION_PACING_MAX_SECONDS
                    )
                )
            # Independent of the sync-status poll below: a clinic whose
            # timezone we cannot read is not an unhealthy integration, and a
            # sync-status failure should not stop us learning the zone.
            try:
                if await self.learn_timezone(
                    institution=row.institution, location=row.location
                ):
                    summary.timezones_learned += 1
            except Exception:  # noqa: BLE001
                logger.warning(
                    "nexhealth timezone learn failed institution=%s location=%s",
                    row.institution.id,
                    row.location.id,
                    exc_info=True,
                )

            try:
                updated = await self.poll_location(
                    institution=row.institution,
                    location=row.location,
                )
                summary.locations_checked += 1
                summary.updated += updated
                row.subscription.last_health_check_at = datetime.now(timezone.utc)
                row.subscription.updated_at = row.subscription.last_health_check_at
                row.subscription.error_metadata = None
                if (
                    row.subscription.status
                    == NexHealthWebhookSubscriptionStatus.PENDING.value
                ):
                    row.subscription.status = (
                        NexHealthWebhookSubscriptionStatus.ACTIVE.value
                    )
            except Exception as exc:  # noqa: BLE001
                summary.failed_locations += 1
                row.subscription.error_metadata = {
                    "type": type(exc).__name__,
                    "reason": "sync_status_poll_failed",
                }
                logger.warning(
                    "nexhealth sync-status poll failed institution=%s location=%s type=%s",
                    row.institution.id,
                    row.location.id,
                    type(exc).__name__,
                )
        return summary

    async def learn_timezone(
        self, *, institution: Institution, location: InstitutionLocation
    ) -> str | None:
        """Reconcile this location's timezone with NexHealth's. Returns the zone
        newly adopted, or None when nothing was written.

        GoTracker reports the clinic's zone on every appointment webhook, so
        that integration learns it for free. NexHealth's appointment payload
        carries no zone at all — only ``start_time``, whose UTC offset says what
        the offset was at one instant and cannot name a zone or its DST rules.
        The practice's location record does carry one, so this has to be a pull.

        Two outcomes, and only the first writes anything:

        * a location still on the ``"UTC"`` default adopts what NexHealth says;
        * a location already configured is **compared, not corrected**. An
          administrator's choice stands, but a disagreement is logged, because
          the alternative is a typo made once at onboarding that silently
          mistimes every send for the life of the clinic.

        Paced to once a day per location: the sweep this hangs off already makes
        one NexHealth call per location every 15 minutes, and background traffic
        shares an allowance of 60 requests/minute per key with reconciliation
        and backfill. ``timezone_checked_at`` is stamped on every attempt, so a
        practice whose record carries no zone is retried tomorrow rather than
        four times an hour forever.
        """
        if not location.nexhealth_location_id:
            return None

        now = datetime.now(timezone.utc)
        last_checked = location.timezone_checked_at
        if last_checked is not None:
            if last_checked.tzinfo is None:
                last_checked = last_checked.replace(tzinfo=timezone.utc)
            if now - last_checked < TIMEZONE_CHECK_INTERVAL:
                return None

        from src.app.pms.nexhealth.adapter import NexHealthAdapter

        adapter = await NexHealthAdapter.create(institution, location)
        try:
            with nexhealth_background_traffic():
                record = await adapter.get_location(str(location.nexhealth_location_id))
        finally:
            await adapter.close()

        # Stamped even when the answer is unusable, so an unreachable or
        # zone-less location costs one request a day rather than every sweep.
        location.timezone_checked_at = now

        if record is None:
            return None

        if not is_timezone_unset(location):
            reported = extract_timezone({"timezone": record.timezone})
            if reported is not None and reported != location.timezone:
                # Deliberately not corrected: we cannot tell an administrator
                # working around a bad PMS record from one who made a typo, and
                # silently overruling the first would be worse than reporting
                # both.
                logger.warning(
                    "nexhealth timezone drift location=%s configured=%s pms_reports=%s",
                    location.id,
                    location.timezone,
                    reported,
                )
            return None

        learned = learn_location_timezone(location, {"timezone": record.timezone})
        if learned is None:
            return None

        # A published campaign's schedule row caches the zone it fires in, so
        # correcting the location alone would leave those campaigns on UTC.
        from src.app.services.automation.schedule_service import (
            WorkflowScheduleService,
        )

        resynced = await WorkflowScheduleService(self.session).resync_for_location(
            str(location.id)
        )
        logger.info(
            "nexhealth timezone learned location=%s zone=%s campaigns_resynced=%s",
            location.id,
            learned,
            resynced,
        )
        return learned

    async def poll_location(
        self, *, institution: Institution, location: InstitutionLocation
    ) -> int:
        from src.app.api.helpers import handle_nexhealth_request
        from src.app.pms.nexhealth.adapter import NexHealthAdapter

        adapter = await NexHealthAdapter.create(institution, location)
        try:
            with nexhealth_background_traffic():
                raw = await handle_nexhealth_request(
                    adapter._client,  # noqa: SLF001
                    "GET",
                    "/sync_status",
                    params=adapter._default_params(),  # noqa: SLF001
                )
        finally:
            await adapter.close()
        payloads = _sync_status_payloads(raw)
        if not payloads:
            payloads = [raw if isinstance(raw, dict) else {}]

        total = 0
        for payload in payloads:
            locations = await self.resolve_locations_for_payload(
                subdomain=str(location.nexhealth_subdomain),
                payload=payload,
            )
            if not locations:
                locations = [location]
            total += await self.upsert_for_locations(
                event="sync_status.poll",
                subdomain=str(location.nexhealth_subdomain),
                locations=locations,
                payload=payload,
            )
        return total

    async def _load_subscription_locations(self) -> list["_SubscriptionLocation"]:
        result = await self.session.execute(
            select(
                NexHealthWebhookSubscription,
                Institution,
                InstitutionLocation,
            )
            .join(
                Institution,
                Institution.id == NexHealthWebhookSubscription.institution_id,
            )
            .join(
                InstitutionLocation,
                InstitutionLocation.id == NexHealthWebhookSubscription.location_id,
            )
            .where(
                Institution.pms_type == "nexhealth",
                NexHealthWebhookSubscription.status.in_(
                    [
                        NexHealthWebhookSubscriptionStatus.ACTIVE.value,
                        NexHealthWebhookSubscriptionStatus.PENDING.value,
                    ]
                ),
                InstitutionLocation.nexhealth_subdomain.is_not(None),
                InstitutionLocation.nexhealth_location_id.is_not(None),
            )
        )
        return [
            _SubscriptionLocation(subscription=sub, institution=inst, location=loc)
            for sub, inst, loc in result.all()
        ]


@dataclass
class _SubscriptionLocation:
    subscription: NexHealthWebhookSubscription
    institution: Institution
    location: InstitutionLocation


def assess_sync_status(row: NexHealthSyncStatus | None) -> SyncStatusAssessment:
    if row is None:
        return SyncStatusAssessment(read_healthy=None, write_healthy=None, stale=True)
    checked_at = row.last_checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return SyncStatusAssessment(
        read_healthy=_status_health(row.read_status),
        write_healthy=_status_health(row.write_status),
        stale=datetime.now(timezone.utc) - checked_at > SYNC_STATUS_STALE_AFTER,
    )


def _status_health(status: str | None) -> bool | None:
    normalized = _clean_str(status)
    if not normalized:
        return None
    value = normalized.lower()
    if value in HEALTHY_SYNC_STATUSES:
        return True
    if value in UNHEALTHY_SYNC_STATUSES:
        return False
    return None


def _sync_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        for key in ("sync_status", "syncstatus"):
            if isinstance(data.get(key), dict):
                return data[key]
        for key in ("sync_statuses", "syncstatuses"):
            if isinstance(data.get(key), list) and data[key]:
                first = data[key][0]
                if isinstance(first, dict):
                    return first
        return data
    return payload if isinstance(payload, dict) else {}


def _sync_status_payloads(raw: dict[str, Any]) -> list[dict[str, Any]]:
    data = raw.get("data") if isinstance(raw, dict) else None
    if isinstance(data, dict):
        for key in ("sync_statuses", "syncstatuses", "sync_status", "syncstatus"):
            values = data.get(key)
            if isinstance(values, list):
                return [item for item in values if isinstance(item, dict)]
            if isinstance(values, dict):
                return [values]
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return [raw] if isinstance(raw, dict) else []


def _locations_payload(payload: dict[str, Any]) -> list | None:
    values = payload.get("locations")
    return values if isinstance(values, list) else None


def _nexhealth_location_ids(payload: dict[str, Any]) -> list[str]:
    values = payload.get("locations")
    ids: list[str] = []
    if isinstance(values, list):
        for value in values:
            if isinstance(value, dict):
                value = value.get("id") or value.get("location_id")
            if value not in (None, ""):
                ids.append(str(value))
    location_id = payload.get("location_id")
    if location_id not in (None, ""):
        ids.append(str(location_id))
    return sorted(set(ids))


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _clean_str(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip() or None
