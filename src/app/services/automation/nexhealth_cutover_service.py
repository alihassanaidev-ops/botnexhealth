"""NexHealth v3 cutover baseline, monitoring, and cleanup assessment.

The cutover runbook needs repeatable evidence from the operational tables rather
than a free-form collection of manual notes. This module keeps that evidence
read-only and JSON-serializable so operators can save a pre-cutover snapshot,
compare it after REST/webhook cutover, and decide whether rollback or cleanup is
safe.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.appointment_working_set import AppointmentWorkingSet
from src.app.models.audit_log import AuditAction, AuditLog, AuditOutcome
from src.app.models.nexhealth_sync_status import NexHealthSyncStatus
from src.app.models.nexhealth_webhook_event import NexHealthWebhookEvent
from src.app.models.nexhealth_webhook_shadow import (
    NexHealthWebhookShadowEvent,
    NexHealthWebhookShadowSubscription,
)
from src.app.models.nexhealth_webhook_subscription import NexHealthWebhookSubscription
from src.app.models.patient_working_set import PatientWorkingSet
from src.app.nexhealth.api_contract import NexHealthAPIContract
from src.app.services.automation.nexhealth_sync_status_service import assess_sync_status

DEFAULT_MONITORING_WINDOW_HOURS = 24
DEFAULT_MIN_STABLE_DAYS = 7

_FAILURE_OUTCOMES = tuple(
    outcome.value
    for outcome in AuditOutcome
    if outcome.value.startswith("FAILURE")
)
_APPOINTMENT_WRITE_ACTIONS = frozenset(
    {
        AuditAction.BOOK_APPOINTMENT.value,
        AuditAction.CANCEL_APPOINTMENT.value,
        AuditAction.CONFIRM_APPOINTMENT.value,
        AuditAction.RESCHEDULE_APPOINTMENT.value,
        AuditAction.UPDATE_APPOINTMENT.value,
    }
)
_PATIENT_LOOKUP_ACTIONS = frozenset({AuditAction.SEARCH_PATIENTS.value})
_SLOT_SEARCH_ACTIONS = frozenset({AuditAction.READ_APPOINTMENT_SLOTS.value})
_OBSERVED_AUDIT_ACTIONS = (
    _APPOINTMENT_WRITE_ACTIONS | _PATIENT_LOOKUP_ACTIONS | _SLOT_SEARCH_ACTIONS
)


@dataclass(frozen=True)
class NexHealthCutoverSnapshot:
    """One JSON-safe view of NexHealth v3 cutover health."""

    collected_at: str
    app_env: str
    api_contract: str
    nex_api_version_header: str
    monitoring_window_hours: int
    live_subscriptions: dict[str, int]
    shadow_subscriptions: dict[str, int]
    projections: dict[str, int]
    sync_statuses: dict[str, int]
    live_webhook_events_recent: dict[str, int]
    shadow_webhook_events: dict[str, int]
    retell_failures_recent: dict[str, int]
    watermarks: dict[str, str | None]


@dataclass(frozen=True)
class NexHealthCutoverAssessment:
    """Rollback and cleanup guidance derived from one or two snapshots."""

    rollback_recommended: bool
    rollback_reasons: list[str]
    monitoring_warnings: list[str]
    cleanup_ready: bool
    cleanup_blockers: list[str]
    deltas: dict[str, int]


class NexHealthCutoverService:
    """Collect cutover evidence from existing operational state."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        app_env: str,
        api_contract: NexHealthAPIContract,
    ) -> None:
        self.session = session
        self.app_env = app_env
        self.api_contract = api_contract

    async def collect_snapshot(
        self, *, monitoring_window_hours: int = DEFAULT_MONITORING_WINDOW_HOURS
    ) -> NexHealthCutoverSnapshot:
        """Collect a pre/post cutover snapshot without mutating state."""
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=monitoring_window_hours)

        live_subscriptions = _with_total(
            await self._group_count(NexHealthWebhookSubscription.status)
        )
        shadow_subscriptions = _with_total(
            await self._group_count(NexHealthWebhookShadowSubscription.status)
        )
        projections = await self._projection_counts(window_start=window_start)
        sync_statuses = await self._sync_status_counts()
        live_events = _with_total(
            await self._group_count(
                NexHealthWebhookEvent.status,
                where=NexHealthWebhookEvent.created_at >= window_start,
            )
        )
        shadow_events = await self._shadow_event_counts()
        retell_failures = await self._retell_failure_counts(window_start=window_start)
        watermarks = await self._watermarks()

        return NexHealthCutoverSnapshot(
            collected_at=_iso(now),
            app_env=self.app_env,
            api_contract=self.api_contract.value,
            nex_api_version_header=self.api_contract.api_version_header,
            monitoring_window_hours=monitoring_window_hours,
            live_subscriptions=live_subscriptions,
            shadow_subscriptions=shadow_subscriptions,
            projections=projections,
            sync_statuses=sync_statuses,
            live_webhook_events_recent=live_events,
            shadow_webhook_events=shadow_events,
            retell_failures_recent=retell_failures,
            watermarks=watermarks,
        )

    async def _group_count(self, column: Any, *, where: Any | None = None) -> dict[str, int]:
        stmt = select(column, func.count()).group_by(column)
        if where is not None:
            stmt = stmt.where(where)
        result = await self.session.execute(stmt)
        return {
            str(key or "unknown"): int(count or 0)
            for key, count in result.all()
        }

    async def _projection_counts(self, *, window_start: datetime) -> dict[str, int]:
        appointment_statuses = await self._group_count(AppointmentWorkingSet.status)
        appointment_total = sum(appointment_statuses.values())
        patient_total = await self._count_rows(PatientWorkingSet)
        appointment_recent = await self._count_rows(
            AppointmentWorkingSet,
            AppointmentWorkingSet.last_synced_at >= window_start,
        )
        patient_recent = await self._count_rows(
            PatientWorkingSet,
            PatientWorkingSet.last_synced_at >= window_start,
        )

        return {
            "appointments_total": appointment_total,
            "appointments_scheduled": appointment_statuses.get("scheduled", 0),
            "appointments_cancelled": appointment_statuses.get("cancelled", 0),
            "appointments_synced_recent": appointment_recent,
            "patients_total": patient_total,
            "patients_synced_recent": patient_recent,
        }

    async def _sync_status_counts(self) -> dict[str, int]:
        result = await self.session.execute(select(NexHealthSyncStatus))
        rows = list(result.scalars().all())
        counts = {
            "total": len(rows),
            "read_unhealthy": 0,
            "read_unknown": 0,
            "write_unhealthy": 0,
            "write_unknown": 0,
            "stale": 0,
        }
        for row in rows:
            assessment = assess_sync_status(row)
            if assessment.read_healthy is False:
                counts["read_unhealthy"] += 1
            elif assessment.read_healthy is None:
                counts["read_unknown"] += 1
            if assessment.write_healthy is False:
                counts["write_unhealthy"] += 1
            elif assessment.write_healthy is None:
                counts["write_unknown"] += 1
            if assessment.stale:
                counts["stale"] += 1
        return counts

    async def _shadow_event_counts(self) -> dict[str, int]:
        parse_counts = await self._group_count(NexHealthWebhookShadowEvent.parse_status)
        resolution_counts = await self._group_count(
            NexHealthWebhookShadowEvent.resolution_status
        )
        return {
            "total": sum(parse_counts.values()),
            "parsed": parse_counts.get("parsed", 0),
            "failed": parse_counts.get("failed", 0),
            "resolved": resolution_counts.get("resolved", 0),
            "unresolved": resolution_counts.get("unresolved", 0),
            "ambiguous": resolution_counts.get("ambiguous", 0),
        }

    async def _retell_failure_counts(self, *, window_start: datetime) -> dict[str, int]:
        stmt = (
            select(AuditLog.action, func.count())
            .where(
                AuditLog.timestamp >= window_start,
                AuditLog.outcome.in_(_FAILURE_OUTCOMES),
                AuditLog.action.in_(_OBSERVED_AUDIT_ACTIONS),
            )
            .group_by(AuditLog.action)
        )
        result = await self.session.execute(stmt)
        action_counts = {
            str(action or "unknown"): int(count or 0)
            for action, count in result.all()
        }
        return {
            "appointment_write_failures": sum(
                action_counts.get(action, 0) for action in _APPOINTMENT_WRITE_ACTIONS
            ),
            "patient_lookup_failures": sum(
                action_counts.get(action, 0) for action in _PATIENT_LOOKUP_ACTIONS
            ),
            "slot_search_failures": sum(
                action_counts.get(action, 0) for action in _SLOT_SEARCH_ACTIONS
            ),
        }

    async def _watermarks(self) -> dict[str, str | None]:
        result = await self.session.execute(
            select(
                func.min(NexHealthWebhookSubscription.last_backfill_at),
                func.max(NexHealthWebhookSubscription.last_backfill_at),
                func.min(NexHealthWebhookSubscription.last_patient_backfill_at),
                func.max(NexHealthWebhookSubscription.last_patient_backfill_at),
                func.min(NexHealthWebhookSubscription.last_reconciliation_at),
                func.max(NexHealthWebhookSubscription.last_reconciliation_at),
                func.min(NexHealthWebhookSubscription.last_patient_reconciliation_at),
                func.max(NexHealthWebhookSubscription.last_patient_reconciliation_at),
            )
        )
        row = result.one()
        return {
            "appointment_backfill_min": _iso(row[0]),
            "appointment_backfill_max": _iso(row[1]),
            "patient_backfill_min": _iso(row[2]),
            "patient_backfill_max": _iso(row[3]),
            "appointment_reconciliation_min": _iso(row[4]),
            "appointment_reconciliation_max": _iso(row[5]),
            "patient_reconciliation_min": _iso(row[6]),
            "patient_reconciliation_max": _iso(row[7]),
        }

    async def _count_rows(self, model: Any, *where: Any) -> int:
        stmt = select(func.count()).select_from(model)
        for condition in where:
            stmt = stmt.where(condition)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)


def assess_cutover(
    current: NexHealthCutoverSnapshot,
    *,
    baseline: NexHealthCutoverSnapshot | None = None,
    stable_since: datetime | None = None,
    min_stable_days: int = DEFAULT_MIN_STABLE_DAYS,
    v2_overlap_removed: bool = False,
    now: datetime | None = None,
) -> NexHealthCutoverAssessment:
    """Assess rollback signals and post-stable cleanup readiness."""
    now = now or datetime.now(timezone.utc)
    deltas: dict[str, int] = {}
    rollback_reasons: list[str] = []
    warnings: list[str] = []

    if current.api_contract != NexHealthAPIContract.STABLE_V3.value:
        warnings.append("REST contract is not stable_v3; cutover is not complete.")

    if baseline is not None:
        _add_delta_reasons(
            baseline=baseline,
            current=current,
            deltas=deltas,
            rollback_reasons=rollback_reasons,
        )

    _add_monitoring_warnings(current, warnings)

    cleanup_blockers = _cleanup_blockers(
        current,
        baseline=baseline,
        stable_since=stable_since,
        min_stable_days=min_stable_days,
        v2_overlap_removed=v2_overlap_removed,
        rollback_reasons=rollback_reasons,
        now=now,
    )

    return NexHealthCutoverAssessment(
        rollback_recommended=bool(rollback_reasons),
        rollback_reasons=rollback_reasons,
        monitoring_warnings=warnings,
        cleanup_ready=not cleanup_blockers,
        cleanup_blockers=cleanup_blockers,
        deltas=deltas,
    )


def snapshot_to_dict(snapshot: NexHealthCutoverSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


def snapshot_from_dict(payload: dict[str, Any]) -> NexHealthCutoverSnapshot:
    return NexHealthCutoverSnapshot(
        collected_at=str(payload["collected_at"]),
        app_env=str(payload["app_env"]),
        api_contract=str(payload["api_contract"]),
        nex_api_version_header=str(payload["nex_api_version_header"]),
        monitoring_window_hours=int(payload["monitoring_window_hours"]),
        live_subscriptions=_int_dict(payload.get("live_subscriptions", {})),
        shadow_subscriptions=_int_dict(payload.get("shadow_subscriptions", {})),
        projections=_int_dict(payload.get("projections", {})),
        sync_statuses=_int_dict(payload.get("sync_statuses", {})),
        live_webhook_events_recent=_int_dict(
            payload.get("live_webhook_events_recent", {})
        ),
        shadow_webhook_events=_int_dict(payload.get("shadow_webhook_events", {})),
        retell_failures_recent=_int_dict(payload.get("retell_failures_recent", {})),
        watermarks={
            str(key): (str(value) if value is not None else None)
            for key, value in dict(payload.get("watermarks", {})).items()
        },
    )


def assessment_to_dict(assessment: NexHealthCutoverAssessment) -> dict[str, Any]:
    return asdict(assessment)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _add_delta_reasons(
    *,
    baseline: NexHealthCutoverSnapshot,
    current: NexHealthCutoverSnapshot,
    deltas: dict[str, int],
    rollback_reasons: list[str],
) -> None:
    _record_projection_delta(
        baseline,
        current,
        deltas,
        rollback_reasons,
        key="appointments_total",
        label="Appointment projection rows",
    )
    _record_projection_delta(
        baseline,
        current,
        deltas,
        rollback_reasons,
        key="patients_total",
        label="Patient projection rows",
    )
    _record_increase_delta(
        baseline,
        current,
        deltas,
        rollback_reasons,
        section="retell_failures_recent",
        key="appointment_write_failures",
        label="Appointment write failures",
    )
    _record_increase_delta(
        baseline,
        current,
        deltas,
        rollback_reasons,
        section="retell_failures_recent",
        key="patient_lookup_failures",
        label="Patient lookup failures",
    )
    _record_increase_delta(
        baseline,
        current,
        deltas,
        rollback_reasons,
        section="retell_failures_recent",
        key="slot_search_failures",
        label="Slot-search failures",
    )
    for key, label in (
        ("failed", "Live webhook processing failures"),
        ("FAILED", "Live webhook processing failures"),
    ):
        _record_increase_delta(
            baseline,
            current,
            deltas,
            rollback_reasons,
            section="live_webhook_events_recent",
            key=key,
            label=label,
        )
    for key, label in (
        ("failed", "Live subscription failures"),
        ("disabled", "Disabled live subscriptions"),
    ):
        _record_increase_delta(
            baseline,
            current,
            deltas,
            rollback_reasons,
            section="live_subscriptions",
            key=key,
            label=label,
        )
    for key, label in (
        ("read_unhealthy", "Read sync-status unhealthy locations"),
        ("write_unhealthy", "Write sync-status unhealthy locations"),
        ("stale", "Stale sync-status locations"),
    ):
        _record_increase_delta(
            baseline,
            current,
            deltas,
            rollback_reasons,
            section="sync_statuses",
            key=key,
            label=label,
        )


def _add_monitoring_warnings(
    snapshot: NexHealthCutoverSnapshot, warnings: list[str]
) -> None:
    checks = (
        (snapshot.live_subscriptions, "failed", "live subscriptions are failed"),
        (snapshot.live_subscriptions, "disabled", "live subscriptions are disabled"),
        (snapshot.live_subscriptions, "pending", "live subscriptions are pending"),
        (
            snapshot.live_webhook_events_recent,
            "FAILED",
            "live webhook events failed in the monitoring window",
        ),
        (
            snapshot.shadow_webhook_events,
            "failed",
            "shadow webhook parse failures are present",
        ),
        (
            snapshot.shadow_webhook_events,
            "unresolved",
            "shadow webhook deliveries are unresolved",
        ),
        (snapshot.sync_statuses, "read_unhealthy", "sync-status reads are unhealthy"),
        (snapshot.sync_statuses, "write_unhealthy", "sync-status writes are unhealthy"),
        (snapshot.sync_statuses, "stale", "sync-status checks are stale"),
        (
            snapshot.retell_failures_recent,
            "appointment_write_failures",
            "appointment writes failed in the monitoring window",
        ),
        (
            snapshot.retell_failures_recent,
            "patient_lookup_failures",
            "patient lookup failed in the monitoring window",
        ),
        (
            snapshot.retell_failures_recent,
            "slot_search_failures",
            "slot search failed in the monitoring window",
        ),
    )
    for counts, key, message in checks:
        value = int(counts.get(key, 0))
        if value > 0:
            warnings.append(f"{value} {message}.")


def _cleanup_blockers(
    snapshot: NexHealthCutoverSnapshot,
    *,
    baseline: NexHealthCutoverSnapshot | None,
    stable_since: datetime | None,
    min_stable_days: int,
    v2_overlap_removed: bool,
    rollback_reasons: list[str],
    now: datetime,
) -> list[str]:
    blockers: list[str] = []
    if snapshot.api_contract != NexHealthAPIContract.STABLE_V3.value:
        blockers.append("REST contract is not stable_v3.")
    if baseline is None:
        blockers.append("No pre-cutover baseline snapshot was supplied.")
    if stable_since is None:
        blockers.append("Stable-since timestamp is required before cleanup.")
    else:
        stable_since = stable_since if stable_since.tzinfo else stable_since.replace(tzinfo=timezone.utc)
        stable_days = (now - stable_since).total_seconds() / 86400
        if stable_days < min_stable_days:
            blockers.append(
                f"Stable v3 window is {stable_days:.1f} days; "
                f"requires at least {min_stable_days} days."
            )
    if not v2_overlap_removed:
        blockers.append("V2-pinned webhook overlap subscriptions are not confirmed removed.")
    if rollback_reasons:
        blockers.append("Open rollback signals must be cleared before cleanup.")
    if _count(snapshot.live_subscriptions, "failed") > 0:
        blockers.append("Live webhook subscriptions still have failed rows.")
    if _count(snapshot.live_subscriptions, "disabled") > 0:
        blockers.append("Live webhook subscriptions still have disabled rows.")
    if _count(snapshot.shadow_subscriptions, "active") > 0:
        blockers.append("Shadow webhook subscriptions are still active.")
    if _count(snapshot.shadow_subscriptions, "pending") > 0:
        blockers.append("Shadow webhook subscriptions are still pending.")
    if _count(snapshot.shadow_webhook_events, "failed") > 0:
        blockers.append("Shadow webhook parse failures remain.")
    return blockers


def _record_projection_delta(
    baseline: NexHealthCutoverSnapshot,
    current: NexHealthCutoverSnapshot,
    deltas: dict[str, int],
    rollback_reasons: list[str],
    *,
    key: str,
    label: str,
) -> None:
    delta = _count(current.projections, key) - _count(baseline.projections, key)
    deltas[key] = delta
    if delta < 0:
        rollback_reasons.append(f"{label} fell by {abs(delta)} after cutover.")


def _record_increase_delta(
    baseline: NexHealthCutoverSnapshot,
    current: NexHealthCutoverSnapshot,
    deltas: dict[str, int],
    rollback_reasons: list[str],
    *,
    section: str,
    key: str,
    label: str,
) -> None:
    current_counts = getattr(current, section)
    baseline_counts = getattr(baseline, section)
    delta = _count(current_counts, key) - _count(baseline_counts, key)
    deltas[f"{section}.{key}"] = delta
    if delta > 0:
        rollback_reasons.append(f"{label} increased by {delta} after cutover.")


def _with_total(counts: dict[str, int]) -> dict[str, int]:
    return {"total": sum(counts.values()), **counts}


def _count(counts: dict[str, int], key: str) -> int:
    return int(counts.get(key, 0) or 0)


def _int_dict(values: Any) -> dict[str, int]:
    return {str(key): int(value or 0) for key, value in dict(values).items()}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()
