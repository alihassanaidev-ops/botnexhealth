"""Project a PMS-shaped trigger payload onto the canonical context vocabulary.

Trigger tasks assemble run context out of whatever the webhook sent, which is
why the builder's field list is full of ``gotracker_status_id`` and
``booked_machine_name``. This module adds the canonical view described in
:mod:`event_catalog` — ``appointment.start_at``, ``appointment.status``,
``patient.first_name`` — on top of what is already there.

Deliberately **additive**. The flat legacy keys stay exactly as they were,
because published definitions branch on them and this is not the place to
rewrite live campaigns. New workflows author against the canonical paths and
port between PMSs; old ones keep working untouched.

Native payloads remain reachable at ``raw.*`` for the cases canonical fields do
not cover, and the builder marks those as PMS-specific.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.app.pms.gotracker.statuses import status_for_id

__all__ = [
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
    put("trigger.source_pms", source_pms or _clean_str(metadata.get("source")) or "none")

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
    put("visit.completed_at", _first(metadata, "flow_changed_at", "appointment_flow_changed_at"))

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
    flow_state = _clean_str(
        _first(metadata, "flow_state", "appointment_flow_state")
    )
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
