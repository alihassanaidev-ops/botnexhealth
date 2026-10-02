"""GoTracker recall-readiness checks shared by launch and scan paths."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any


@dataclass(frozen=True)
class GoTrackerRecallHistoryAssessment:
    complete: bool
    reason: str
    message: str
    metadata: dict[str, Any]


_HISTORY_OBJECT_KEYS = (
    "recall_history_sync",
    "recallHistorySync",
    "recall_history",
    "recallHistory",
    "appointment_history_sync",
    "appointmentHistorySync",
    "appointment_history",
    "appointmentHistory",
    "history_sync",
    "historySync",
    "history",
)
_EXPLICIT_COMPLETE_KEYS = (
    "recall_history_complete",
    "recallHistoryComplete",
    "history_sync_complete",
    "historySyncComplete",
    "appointment_history_complete",
    "appointmentHistoryComplete",
    "appointments_history_complete",
    "appointmentsHistoryComplete",
    "initial_sync_complete",
    "initialSyncComplete",
    "backfill_complete",
    "backfillComplete",
)
_GENERIC_COMPLETE_KEYS = ("complete", "completed", "is_complete", "isComplete")
_EXPLICIT_STATUS_KEYS = (
    "recall_history_status",
    "recallHistoryStatus",
    "history_sync_status",
    "historySyncStatus",
    "appointment_history_status",
    "appointmentHistoryStatus",
)
_GENERIC_STATUS_KEYS = ("status", "state", "phase")
_COMPLETE_STATUS_VALUES = {
    "complete",
    "completed",
    "ready",
    "synced",
    "succeeded",
    "success",
}
_INCOMPLETE_STATUS_VALUES = {
    "backfilling",
    "building",
    "in_progress",
    "in-progress",
    "pending",
    "running",
    "syncing",
}
_PROGRESS_KEYS = (
    "history_progress_percent",
    "historyProgressPercent",
    "progress_percent",
    "progressPercent",
    "percent_complete",
    "percentComplete",
)
_METADATA_KEYS = (
    *_EXPLICIT_COMPLETE_KEYS,
    *_GENERIC_COMPLETE_KEYS,
    *_EXPLICIT_STATUS_KEYS,
    *_GENERIC_STATUS_KEYS,
    *_PROGRESS_KEYS,
    "started_at",
    "startedAt",
    "completed_at",
    "completedAt",
    "last_synced_at",
    "lastSyncedAt",
    "synced_through",
    "syncedThrough",
    "appointments_synced",
    "appointmentsSynced",
    "appointments_total",
    "appointmentsTotal",
)


async def assess_gotracker_recall_history(
    adapter: Any,
) -> GoTrackerRecallHistoryAssessment:
    """Read and assess GoTracker history-sync completion from an adapter.

    GoTracker recall eligibility depends on complete appointment history. The
    synchronizer owns that progress signal, so the platform refuses recall when
    the adapter cannot expose it or when the payload is not explicitly complete.
    """

    method = getattr(adapter, "get_recall_history_sync_status", None)
    if not callable(method):
        return GoTrackerRecallHistoryAssessment(
            complete=False,
            reason="history_sync_status_unavailable",
            message=(
                "GoTracker Synchronizer did not expose appointment-history sync "
                "status; recall will not run for this location."
            ),
            metadata={"adapter_source": getattr(adapter, "source", None)},
        )

    try:
        payload = method()
        if inspect.isawaitable(payload):
            payload = await payload
    except NotImplementedError:
        return GoTrackerRecallHistoryAssessment(
            complete=False,
            reason="history_sync_status_unavailable",
            message=(
                "GoTracker Synchronizer did not expose appointment-history sync "
                "status; recall will not run for this location."
            ),
            metadata={"adapter_source": getattr(adapter, "source", None)},
        )
    except Exception as exc:  # noqa: BLE001 - fail closed, without logging PHI.
        return GoTrackerRecallHistoryAssessment(
            complete=False,
            reason="history_sync_status_error",
            message=(
                "GoTracker appointment-history sync status could not be read; "
                "recall will not run for this location."
            ),
            metadata={
                "adapter_source": getattr(adapter, "source", None),
                "error_type": type(exc).__name__,
            },
        )

    if not isinstance(payload, dict):
        return GoTrackerRecallHistoryAssessment(
            complete=False,
            reason="history_sync_status_unrecognized",
            message=(
                "GoTracker Synchronizer returned an unrecognized history-sync "
                "status; recall will not run for this location."
            ),
            metadata={"adapter_source": getattr(adapter, "source", None)},
        )

    return assess_gotracker_recall_history_payload(payload)


def assess_gotracker_recall_history_payload(
    payload: dict[str, Any],
) -> GoTrackerRecallHistoryAssessment:
    """Classify a synchronizer sync-status payload for recall safety."""

    status_payload, nested = _history_payload(payload)
    metadata = _status_metadata(status_payload)

    explicit_complete = _bool_field(payload, *_EXPLICIT_COMPLETE_KEYS)
    if explicit_complete is None:
        explicit_complete = _bool_field(status_payload, *_EXPLICIT_COMPLETE_KEYS)
    if explicit_complete is None and nested:
        explicit_complete = _bool_field(status_payload, *_GENERIC_COMPLETE_KEYS)
    if explicit_complete is not None:
        return _assessment_for_bool(explicit_complete, metadata)

    explicit_status = _string_field(payload, *_EXPLICIT_STATUS_KEYS)
    if explicit_status is None:
        explicit_status = _string_field(status_payload, *_EXPLICIT_STATUS_KEYS)
    if explicit_status is None and nested:
        explicit_status = _string_field(status_payload, *_GENERIC_STATUS_KEYS)
    if explicit_status:
        normalized = explicit_status.strip().casefold()
        if normalized in _COMPLETE_STATUS_VALUES:
            return _assessment_for_bool(True, metadata)
        if normalized in _INCOMPLETE_STATUS_VALUES:
            return _assessment_for_bool(False, metadata)

    progress = _number_field(status_payload, *_PROGRESS_KEYS)
    if progress is not None:
        return _assessment_for_bool(progress >= 100, metadata)

    return GoTrackerRecallHistoryAssessment(
        complete=False,
        reason="history_sync_status_unrecognized",
        message=(
            "GoTracker Synchronizer did not explicitly confirm appointment-history "
            "sync completion; recall will not run for this location."
        ),
        metadata=metadata,
    )


def _assessment_for_bool(
    complete: bool, metadata: dict[str, Any]
) -> GoTrackerRecallHistoryAssessment:
    return GoTrackerRecallHistoryAssessment(
        complete=complete,
        reason="history_sync_complete" if complete else "history_sync_incomplete",
        message=(
            "GoTracker appointment-history sync is complete for recall."
            if complete
            else (
                "GoTracker appointment-history sync is not complete; recall will "
                "not run for this location."
            )
        ),
        metadata=metadata,
    )


def _history_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    data = payload.get("data")
    if isinstance(data, dict):
        nested, found = _history_payload(data)
        if found:
            return nested, True
        return nested, False
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        nested, found = _history_payload(data[0])
        return nested, found
    for key in _HISTORY_OBJECT_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            return value, True
    return payload, False


def _status_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in _METADATA_KEYS:
        value = payload.get(key)
        if _metadata_value_allowed(value):
            metadata[key] = value
    return metadata


def _metadata_value_allowed(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(isinstance(item, str | int | float | bool) for item in value)
    return False


def _bool_field(payload: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "1", "yes", "complete", "completed"}:
                return True
            if normalized in {"false", "0", "no", "incomplete", "pending"}:
                return False
    return None


def _string_field(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _number_field(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                continue
    return None
