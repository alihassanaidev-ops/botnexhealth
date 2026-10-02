"""Unit tests for NexHealth v3 cutover reporting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.app.nexhealth.api_contract import NexHealthAPIContract
from src.app.services.automation.nexhealth_cutover_service import (
    NexHealthCutoverSnapshot,
    assess_cutover,
    parse_iso_datetime,
    snapshot_from_dict,
    snapshot_to_dict,
)


def _snapshot(**overrides) -> NexHealthCutoverSnapshot:
    values = {
        "collected_at": "2026-08-15T00:00:00+00:00",
        "app_env": "staging",
        "api_contract": NexHealthAPIContract.STABLE_V3.value,
        "nex_api_version_header": "v3.0.0",
        "monitoring_window_hours": 24,
        "live_subscriptions": {
            "total": 3,
            "active": 3,
            "pending": 0,
            "disabled": 0,
            "failed": 0,
        },
        "shadow_subscriptions": {
            "total": 0,
            "active": 0,
            "pending": 0,
            "disabled": 0,
            "failed": 0,
        },
        "projections": {
            "appointments_total": 100,
            "appointments_scheduled": 95,
            "appointments_cancelled": 5,
            "appointments_synced_recent": 100,
            "patients_total": 250,
            "patients_synced_recent": 250,
        },
        "sync_statuses": {
            "total": 3,
            "read_unhealthy": 0,
            "read_unknown": 0,
            "write_unhealthy": 0,
            "write_unknown": 0,
            "stale": 0,
        },
        "live_webhook_events_recent": {
            "total": 10,
            "COMPLETED": 10,
            "FAILED": 0,
            "PROCESSING": 0,
        },
        "shadow_webhook_events": {
            "total": 3,
            "parsed": 3,
            "failed": 0,
            "resolved": 3,
            "unresolved": 0,
            "ambiguous": 0,
        },
        "retell_failures_recent": {
            "appointment_write_failures": 0,
            "patient_lookup_failures": 0,
            "slot_search_failures": 0,
        },
        "watermarks": {
            "appointment_backfill_min": "2026-08-14T22:00:00+00:00",
            "appointment_backfill_max": "2026-08-14T22:05:00+00:00",
            "patient_backfill_min": "2026-08-14T22:10:00+00:00",
            "patient_backfill_max": "2026-08-14T22:15:00+00:00",
            "appointment_reconciliation_min": None,
            "appointment_reconciliation_max": None,
            "patient_reconciliation_min": None,
            "patient_reconciliation_max": None,
        },
    }
    values.update(overrides)
    return NexHealthCutoverSnapshot(**values)


def test_assessment_stays_clean_when_post_cutover_matches_baseline() -> None:
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    stable_since = now - timedelta(days=8)

    assessment = assess_cutover(
        _snapshot(),
        baseline=_snapshot(),
        stable_since=stable_since,
        v2_overlap_removed=True,
        now=now,
    )

    assert assessment.rollback_recommended is False
    assert assessment.rollback_reasons == []
    assert assessment.cleanup_ready is True
    assert assessment.cleanup_blockers == []


def test_assessment_flags_projection_gaps_and_failure_increases() -> None:
    current = _snapshot(
        live_subscriptions={
            "total": 3,
            "active": 2,
            "pending": 0,
            "disabled": 0,
            "failed": 1,
        },
        projections={
            "appointments_total": 97,
            "appointments_scheduled": 92,
            "appointments_cancelled": 5,
            "appointments_synced_recent": 97,
            "patients_total": 249,
            "patients_synced_recent": 249,
        },
        sync_statuses={
            "total": 3,
            "read_unhealthy": 1,
            "read_unknown": 0,
            "write_unhealthy": 0,
            "write_unknown": 0,
            "stale": 1,
        },
        live_webhook_events_recent={
            "total": 11,
            "COMPLETED": 10,
            "FAILED": 1,
            "PROCESSING": 0,
        },
        retell_failures_recent={
            "appointment_write_failures": 2,
            "patient_lookup_failures": 1,
            "slot_search_failures": 1,
        },
    )

    assessment = assess_cutover(current, baseline=_snapshot())

    assert assessment.rollback_recommended is True
    assert "Appointment projection rows fell by 3 after cutover." in (
        assessment.rollback_reasons
    )
    assert "Patient projection rows fell by 1 after cutover." in (
        assessment.rollback_reasons
    )
    assert "Appointment write failures increased by 2 after cutover." in (
        assessment.rollback_reasons
    )
    assert "Patient lookup failures increased by 1 after cutover." in (
        assessment.rollback_reasons
    )
    assert "Slot-search failures increased by 1 after cutover." in (
        assessment.rollback_reasons
    )
    assert "Live subscription failures increased by 1 after cutover." in (
        assessment.rollback_reasons
    )
    assert assessment.deltas["appointments_total"] == -3
    assert assessment.deltas["retell_failures_recent.slot_search_failures"] == 1


def test_cleanup_blocks_until_stable_window_and_overlap_confirmation() -> None:
    current = _snapshot(
        api_contract=NexHealthAPIContract.LEGACY_V2.value,
        nex_api_version_header="v2",
        shadow_subscriptions={
            "total": 1,
            "active": 1,
            "pending": 0,
            "disabled": 0,
            "failed": 0,
        },
        shadow_webhook_events={
            "total": 1,
            "parsed": 0,
            "failed": 1,
            "resolved": 0,
            "unresolved": 1,
            "ambiguous": 0,
        },
    )
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)

    assessment = assess_cutover(
        current,
        stable_since=now - timedelta(days=2),
        now=now,
    )

    assert assessment.cleanup_ready is False
    assert "REST contract is not stable_v3." in assessment.cleanup_blockers
    assert "No pre-cutover baseline snapshot was supplied." in assessment.cleanup_blockers
    assert any("requires at least 7 days" in item for item in assessment.cleanup_blockers)
    assert (
        "V2-pinned webhook overlap subscriptions are not confirmed removed."
        in assessment.cleanup_blockers
    )
    assert "Shadow webhook subscriptions are still active." in assessment.cleanup_blockers
    assert "Shadow webhook parse failures remain." in assessment.cleanup_blockers


def test_snapshot_serialization_round_trips_counts_and_watermarks() -> None:
    original = _snapshot()

    restored = snapshot_from_dict(snapshot_to_dict(original))

    assert restored == original


def test_parse_iso_datetime_accepts_z_suffix_and_adds_utc_to_naive_values() -> None:
    assert parse_iso_datetime("2026-08-15T10:00:00Z") == datetime(
        2026, 8, 15, 10, 0, tzinfo=timezone.utc
    )
    assert parse_iso_datetime("2026-08-15T10:00:00") == datetime(
        2026, 8, 15, 10, 0, tzinfo=timezone.utc
    )
