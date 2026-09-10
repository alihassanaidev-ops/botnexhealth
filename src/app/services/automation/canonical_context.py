"""Assemble workflow context without losing PMS-native appointment fields.

New workflows author appointment logic against ``nexhealth_payload.*`` or
``gotracker_payload.*``. The flat and ``appointment.*`` aliases are retained
only so already-published workflows keep running while native event facts take
precedence over older projection values.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.app.pms.gotracker.statuses import status_for_id

__all__ = [
    "build_appointment_trigger_context",
    "canonical_context",
    "merge_canonical_context",
    "canonical_appointment_status",
    "appointment_event_key",
]


# GoTracker sends its own disposition ids; NexHealth sends words. Both land on
# the small neutral vocabulary declared in the event catalog.
_NEXHEALTH_STATUS_MAP = {
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "confirmed": "booked",
    "scheduled": "booked",
    "booked": "booked",
    "arrived": "waiting",
    "checked_in": "waiting",
    "completed": "booked",
    "no_show": "no_show",
    "noshow": "no_show",
    "missed": "no_show",
    "pending": "pending",
}


def canonical_appointment_status(
    *, status_id: Any = None, raw_status: Any = None, source_pms: str | None = None
) -> str | None:
    """Neutral status word from either PMS's representation."""
    if source_pms == "gotracker" or status_id is not None:
        status = status_for_id(status_id)
        if status is not None:
            return status.semantics
    text = _clean_str(raw_status)
    if text is None:
        return None
    return _NEXHEALTH_STATUS_MAP.get(text.casefold(), text.casefold())


def canonical_context(
    metadata: Mapping[str, Any],
    *,
    event_key: str,
    source_pms: str | None = None,
    occurred_at: datetime | None = None,
    location_name: str | None = None,
    location_timezone: str | None = None,
) -> dict[str, Any]:
    """Build the canonical section for one trigger payload.

    Only keys with a resolvable value are emitted. A canonical field that is
    absent must stay absent, because the filter DSL treats "missing" as "cannot
    match" — writing an empty string instead would turn a missing appointment
    reason into one that matches ``is_not_null``.
    """
    out: dict[str, Any] = {}

    def put(path: str, value: Any) -> None:
        if value is None or value == "":
            return
        _assign(out, path, value)

    put("trigger.key", event_key)
    put(
        "trigger.occurred_at",
        (occurred_at or datetime.now(tz=timezone.utc)).isoformat(),
    )
    put(
        "trigger.source_pms", source_pms or _clean_str(metadata.get("source")) or "none"
    )

    # --- patient -----------------------------------------------------------
    put("patient.id", _first(metadata, "contact_id", "patient_id"))
    put("patient.first_name", _first(metadata, "patient_first_name", "first_name"))
    put("patient.last_name", _first(metadata, "patient_last_name", "last_name"))
    put("patient.preferred_language", metadata.get("patient_preferred_language"))
    put("patient.status", _first(metadata, "patient_workflow_status", "patient_status"))
    # The internal-status trigger reports a transition, so the previous value and
    # which field moved are part of the vocabulary, not just the landing state.
    put("patient.status_previous", metadata.get("patient_status_previous"))
    put("patient.status_field", metadata.get("patient_status_field"))

    # --- location ----------------------------------------------------------
    put("location.id", metadata.get("location_id"))
    put("location.name", location_name or metadata.get("location_name"))
    put("location.timezone", location_timezone or metadata.get("location_timezone"))

    # --- appointment -------------------------------------------------------
    put(
        "appointment.id",
        _first(metadata, "appointment_id", "gotracker_appointment_id"),
    )
    put(
        "appointment.start_at",
        _first(
            metadata,
            "appointment_datetime",
            "appointment_start_time",
            "appointment_at",
        )
        or _combine_date_time(
            metadata.get("appointment_date"), metadata.get("appointment_time")
        ),
    )
    put(
        "appointment.duration_minutes",
        _duration_minutes(metadata.get("appointment_duration")),
    )
    put(
        "appointment.status",
        canonical_appointment_status(
            status_id=_first(metadata, "gotracker_status_id", "appointment_status_id"),
            raw_status=metadata.get("appointment_status"),
            source_pms=source_pms or _clean_str(metadata.get("source")),
        ),
    )
    put("appointment.reason", metadata.get("appointment_reason"))
    reasons = metadata.get("appointment_reasons") or metadata.get("gotracker_reasons")
    if isinstance(reasons, (list, tuple)) and reasons:
        put("appointment.reasons", list(reasons))
    # GoTracker sends `is_confirmed`; NexHealth's route emits
    # `appointment_confirmed`. Reading only the first meant the canonical field
    # was absent on every NexHealth appointment even though the value was there.
    put(
        "appointment.is_confirmed",
        _as_bool(_first(metadata, "is_confirmed", "appointment_confirmed")),
    )
    put("appointment.is_recall", _as_bool(metadata.get("is_recall")))
    put("appointment.provider.id", metadata.get("provider_id"))
    put("appointment.provider.name", metadata.get("provider_name"))
    put("appointment.type.id", metadata.get("appointment_type_id"))
    put(
        "appointment.type.name",
        _first(metadata, "appointment_type_name", "appointment_type"),
    )
    put("appointment.original_start_at", metadata.get("original_date"))

    # --- visit -------------------------------------------------------------
    # Both PMSs land here: GoTracker through Chair Flow's FlowChange, NexHealth
    # through the derived completion sweep.
    put(
        "visit.completed_at",
        _first(metadata, "flow_changed_at", "appointment_flow_changed_at"),
    )

    # --- call --------------------------------------------------------------
    put("call.id", metadata.get("call_id"))
    put("call.direction", metadata.get("call_direction"))
    put("call.outcome", _first(metadata, "call_outcome", "call_status"))
    put("call.duration_seconds", metadata.get("call_duration_seconds"))
    put("call.callback_at", _first(metadata, "callback_at", "preferred_callback_time"))

    # --- inbound message ---------------------------------------------------
    put(
        "message.id",
        _first(metadata, "inbound_sms_message_id", "email_reply_message_id"),
    )
    if metadata.get("inbound_sms_message_id"):
        put("message.channel", "sms")
    elif metadata.get("email_reply_message_id"):
        put("message.channel", "email")
    put("message.body", _first(metadata, "sms_reply_body", "email_reply_body"))
    put("message.intent", _first(metadata, "sms_reply_intent", "email_reply_intent"))

    # --- enquiry -----------------------------------------------------------
    # The intake pipeline already ships a nested `enquiry` dict; these paths name
    # the same values so the builder's field picker and the payload agree.
    put("enquiry.source", metadata.get("enquiry_source"))
    put("enquiry.status", metadata.get("enquiry_status"))
    created = _as_bool(metadata.get("enquiry_created"))
    if created is not None:
        put("enquiry.created", created)
    matched = _as_bool(metadata.get("matched_existing_contact"))
    if matched is not None:
        put("enquiry.matched_existing_contact", matched)

    # --- recall ------------------------------------------------------------
    put("recall.due_at", _first(metadata, "recall_due_date", "due_date"))
    put("recall.type", _first(metadata, "recall_type_name", "recall_type"))
    put("recall.last_visit_at", _first(metadata, "last_visit_date", "last_visit_at"))

    return out


def merge_canonical_context(
    metadata: dict[str, Any],
    *,
    event_key: str,
    source_pms: str | None = None,
    occurred_at: datetime | None = None,
    location_name: str | None = None,
    location_timezone: str | None = None,
) -> dict[str, Any]:
    """Return ``metadata`` with the canonical section merged in.

    Flat legacy keys are never touched — a published definition reading
    ``appointment_status`` keeps seeing exactly what it saw before.

    Inside a shared namespace the canonical value **wins**. Two trigger paths
    already build an ``appointment`` dict whose ``status`` is the raw PMS word,
    and letting that shadow the normalized one would mean
    ``appointment.status`` returned ``"scheduled"`` on NexHealth and ``"booked"``
    on GoTracker — exactly the divergence this layer exists to remove. Legacy
    sub-keys the canonical shape does not define (``start_time``,
    ``appointment_type_id``, …) are preserved.
    """
    canonical = canonical_context(
        metadata,
        event_key=event_key,
        source_pms=source_pms,
        occurred_at=occurred_at,
        location_name=location_name,
        location_timezone=location_timezone,
    )
    merged = dict(metadata)
    for key, value in canonical.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        elif key not in merged:
            merged[key] = value
    return merged


def build_appointment_trigger_context(
    projection: Mapping[str, Any],
    event_metadata: Mapping[str, Any],
    *,
    event_key: str | None = None,
) -> dict[str, Any]:
    """Build the final appointment context consumed by workflow filters.

    The local projection fills gaps left by a webhook, while non-null facts from
    the current event win.  Provider-neutral aliases are derived only after that
    merge, so a projection's storage value (for example NexHealth ``scheduled``)
    cannot replace the workflow meaning (``booked``).
    """
    merged = _deep_merge_non_null(dict(projection), event_metadata)
    source_pms = _appointment_source_pms(merged)
    _ensure_native_appointment_payload(merged, source_pms)

    status = canonical_appointment_status(
        status_id=_first(merged, "gotracker_status_id", "appointment_status_id"),
        raw_status=merged.get("appointment_status"),
        source_pms=source_pms,
    )
    if status is not None:
        # Compatibility for already-published definitions. New definitions use
        # the provider-native namespaces or ``appointment.status``.
        merged["appointment_status"] = status

    if source_pms == "nexhealth" and not _clean_str(merged.get("appointment_reason")):
        type_name = _clean_str(
            _first(merged, "appointment_type_name", "appointment_type")
        )
        if type_name is not None:
            merged["appointment_reason"] = type_name
            merged["appointment_reasons"] = [type_name]

    key = event_key or appointment_event_key(merged)
    return merge_canonical_context(
        merged,
        event_key=key,
        source_pms=source_pms,
    )


def _ensure_native_appointment_payload(
    metadata: dict[str, Any], source_pms: str | None
) -> None:
    """Fill the curated PMS namespace when a producer supplied only flat fields."""
    if source_pms == "nexhealth":
        status = (_clean_str(metadata.get("appointment_status")) or "").casefold()
        inferred = {
            "event": metadata.get("event"),
            "appointment": {
                "id": _first(metadata, "nexhealth_appointment_id", "appointment_id"),
                "location_id": _first(metadata, "nexhealth_location_id", "location_id"),
                "patient_id": _first(metadata, "nexhealth_patient_id", "patient_id"),
                "provider_id": metadata.get("provider_id"),
                "appointment_type_id": metadata.get("appointment_type_id"),
                "appointment_type_name": _first(
                    metadata, "appointment_type_name", "appointment_type"
                ),
                "start_time": _first(
                    metadata,
                    "appointment_datetime",
                    "appointment_start_time",
                    "appointment_at",
                ),
                "confirmed": _first(metadata, "appointment_confirmed", "is_confirmed"),
                "cancelled": status in {"cancelled", "canceled"},
            },
        }
        existing = metadata.get("nexhealth_payload")
        metadata["nexhealth_payload"] = _deep_merge_non_null(
            inferred,
            existing if isinstance(existing, Mapping) else {},
        )
    elif source_pms == "gotracker":
        inferred = {
            "event": metadata.get("event"),
            "appointment": {
                "id": _first(metadata, "gotracker_appointment_id", "appointment_id"),
                "contact_id": _first(
                    metadata, "gotracker_contact_id", "contact_source_id"
                ),
                "date": metadata.get("appointment_date"),
                "time": metadata.get("appointment_time"),
                "reasons": _first(metadata, "gotracker_reasons", "appointment_reasons"),
                "provider_id": _first(metadata, "gotracker_provider_id", "provider_id"),
                "schedule_column_id": _first(
                    metadata,
                    "gotracker_schedule_column_id",
                    "schedule_column_id",
                ),
                "status_id": _first(
                    metadata, "gotracker_status_id", "appointment_status_id"
                ),
                "status": metadata.get("appointment_status")
                or canonical_appointment_status(
                    status_id=_first(
                        metadata, "gotracker_status_id", "appointment_status_id"
                    ),
                    source_pms="gotracker",
                ),
                "duration": metadata.get("appointment_duration"),
                "is_confirmed": metadata.get("is_confirmed"),
                "is_preconfirmed": metadata.get("is_preconfirmed"),
                "flow_state": metadata.get("flow_state"),
                "flow_change": metadata.get("flow_changed_at"),
            },
        }
        existing = metadata.get("gotracker_payload")
        metadata["gotracker_payload"] = _deep_merge_non_null(
            inferred,
            existing if isinstance(existing, Mapping) else {},
        )


def _appointment_source_pms(metadata: Mapping[str, Any]) -> str | None:
    explicit = _clean_str(metadata.get("pms_source"))
    if explicit in {"nexhealth", "gotracker"}:
        return explicit
    source = (_clean_str(metadata.get("source")) or "").casefold()
    if source.startswith("nexhealth") or "nexhealth_payload" in metadata:
        return "nexhealth"
    if source.startswith("gotracker") or "gotracker_payload" in metadata:
        return "gotracker"
    return explicit or source or None


def _deep_merge_non_null(
    base: dict[str, Any], overlay: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge current-event facts over a projection without erasing known values."""
    out = dict(base)
    for key, value in overlay.items():
        if value is None:
            continue
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            out[key] = _deep_merge_non_null(existing, value)
        else:
            out[key] = value
    return out


def _deep_merge(legacy: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any]:
    """Canonical values win; legacy-only keys survive."""
    out = dict(legacy)
    for key, value in canonical.items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            out[key] = _deep_merge(existing, value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _assign(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def _first(metadata: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = metadata.get(key)
        if value is not None and value != "":
            return value
    return None


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _combine_date_time(date_value: Any, time_value: Any) -> str | None:
    """GoTracker sends the date and the time of day as separate fields."""
    date_text = _clean_str(date_value)
    if date_text is None:
        return None
    time_text = _clean_str(time_value)
    if time_text is None:
        return date_text
    # `AppointmentDate` carries a midnight component that the real time replaces.
    day = date_text.split("T", 1)[0]
    return f"{day}T{time_text}"


def _duration_minutes(value: Any) -> int | None:
    """Accept ``00:15:00``, ``15``, or ``PT15M``-ish values."""
    text = _clean_str(value)
    if text is None:
        return None
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    if len(parts) >= 2 and all(part.isdigit() for part in parts[:2]):
        return int(parts[0]) * 60 + int(parts[1])
    return None


def appointment_event_key(metadata: Mapping[str, Any]) -> str:
    """Best canonical event key for an appointment trigger payload.

    Trigger tasks predate the event catalog, so the key is inferred rather than
    passed: the native webhook name where one is present, otherwise the
    appointment's own state. This is what lets an existing trigger path publish
    canonical context without every producer being rewritten first.

    NexHealth needs the status fallback rather than the native-name check: it has
    no distinct cancel event, only a ``cancelled`` flag on ``appointment_updated``
    that its webhook route folds into ``appointment_status`` before we see it.
    """
    flow_state = _clean_str(_first(metadata, "flow_state", "appointment_flow_state"))
    if flow_state and flow_state.casefold() == "completed":
        return "appointment.completed"

    native = _clean_str(metadata.get("event")) or ""
    lowered = native.casefold()
    if "cancel" in lowered:
        return "appointment.cancelled"
    if "reschedul" in lowered:
        return "appointment.rescheduled"

    status = canonical_appointment_status(
        status_id=_first(metadata, "gotracker_status_id", "appointment_status_id"),
        raw_status=metadata.get("appointment_status"),
        source_pms=_clean_str(metadata.get("source")),
    )
    if status == "cancelled":
        return "appointment.cancelled"
    if status == "no_show":
        return "appointment.no_show"
    if status == "waiting":
        return "appointment.checked_in"

    if _as_bool(metadata.get("is_confirmed")):
        return "appointment.confirmed"
    return "appointment.booked"
