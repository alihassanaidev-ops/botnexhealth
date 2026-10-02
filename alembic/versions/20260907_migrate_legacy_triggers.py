"""Rewrite published workflow definitions onto the current trigger vocabulary.

Revision ID: 20260907_legacy_triggers
Revises: 20260905_staff_notif
Create Date: 2026-09-07

The eleven pre-rearchitecture trigger types were replaced by six.
``upconvert_legacy_trigger`` converts their stored JSON on *read*, so campaigns
kept running — but the database still holds the old shape, and the builder is
handed that raw JSON. The result is a trigger panel whose dropdown has no entry
for the campaign's own trigger, so opening one and touching the picker silently
replaces it.

This rewrites them at rest. The conversion is a frozen copy of the one in
``definition_schema`` rather than an import, because a migration has to keep
working against the code as it was when the migration ran.

Only definitions that still carry a retired type are touched, and each rewrite
is re-serialised into the same shape the loader would have produced, so a
campaign behaves identically before and after.
"""

from __future__ import annotations

import hashlib
import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "20260907_legacy_triggers"
down_revision = "20260905_staff_notif"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

LEGACY_TRIGGER_TYPES = {
    "appointment_offset",
    "appointment_state_changed",
    "recall_scan",
    "bulk_import",
    "enquiry_received",
    "callback_requested",
    "patient_status_changed",
    "sms_reply",
    "email_reply",
}

# GoTracker disposition id -> the canonical event that status change represents.
# Frozen from `pms/gotracker/statuses.py` at the time of writing.
_STATUS_EVENT = {
    1: "appointment.booked",
    2: "appointment.checked_in",
    3: "appointment.cancelled",
    5: "appointment.no_show",
    6: "appointment.cancelled",
    8: "appointment.cancelled",
    9: "appointment.checked_in",
}


def _convert(trigger: dict) -> dict:
    """One retired trigger dict in the current shape."""
    kind = trigger.get("type")
    common: dict = {}
    if trigger.get("filter") is not None:
        common["filter"] = trigger["filter"]
    if trigger.get("campaign_goal"):
        common["campaign_goal"] = trigger["campaign_goal"]

    if kind == "appointment_offset":
        # The offset decided when the reminder fired, so it stays as the
        # interval on the reminder event rather than being lost.
        return {
            "type": "event",
            "event_keys": ["appointment.reminder_due"],
            "reminder_offset_hours": int(trigger.get("offset_hours") or 0),
            **common,
        }

    if kind == "appointment_state_changed":
        keys: list[str] = []
        for status_id in trigger.get("status_ids") or []:
            mapped = _STATUS_EVENT.get(status_id)
            if mapped and mapped not in keys:
                keys.append(mapped)
        for flow_state in trigger.get("flow_states") or []:
            if str(flow_state).strip().casefold() == "completed":
                if "appointment.completed" not in keys:
                    keys.append("appointment.completed")
        if trigger.get("confirmed") is True and "appointment.confirmed" not in keys:
            keys.append("appointment.confirmed")
        if not keys:
            # A matcher we cannot map (preconfirmed, a bespoke Chair Flow state)
            # still has to enroll on something observable.
            keys = ["appointment.completed"]
        converted = {"type": "event", "event_keys": keys, **common}
        if trigger.get("max_followup_delay_hours") is not None:
            converted["max_followup_delay_hours"] = trigger["max_followup_delay_hours"]
        return converted

    if kind == "recall_scan":
        return {
            "type": "schedule",
            # The old scanner ran hourly and relied on a monthly idempotency key
            # to stay quiet. A daily morning tick is the same behaviour without
            # 23 wasted sweeps a day.
            "cron": "0 9 * * *",
            "timezone_mode": "location",
            "source": {
                "kind": "pms_recall",
                "recall_interval_months": int(
                    trigger.get("recall_interval_months") or 6
                ),
                "reenrollment_cooldown_days": int(
                    trigger.get("recall_reenrollment_cooldown_days") or 90
                ),
            },
            **common,
        }

    if kind == "bulk_import":
        return {"type": "manual", **common}

    if kind == "enquiry_received":
        return {"type": "event", "event_keys": ["enquiry.received"], **common}

    if kind == "callback_requested":
        # The old trigger fired only for calls already classified
        # needs_callback, so the classification becomes an explicit filter.
        callback_filter = {
            "kind": "rule",
            "field": "call.outcome",
            "op": "eq",
            "value": "needs_callback",
        }
        existing = common.pop("filter", None)
        merged = (
            {"kind": "group", "op": "and", "children": [existing, callback_filter]}
            if existing is not None
            else callback_filter
        )
        return {
            "type": "event",
            "event_keys": ["call.inbound.completed"],
            "filter": merged,
            **common,
        }

    if kind == "patient_status_changed":
        return {
            "type": "internal_status",
            "field": "patient_workflow_status",
            "to_statuses": list(trigger.get("statuses") or []),
            **common,
        }

    if kind in {"sms_reply", "email_reply"}:
        return {
            "type": "inbound_message",
            "channels": ["sms" if kind == "sms_reply" else "email"],
            "tokens": list(trigger.get("tokens") or []),
            **common,
        }

    return trigger


def _rewrite(definition: dict) -> dict | None:
    """The definition in current shape, or None when nothing needed changing."""
    triggers = definition.get("triggers")
    singular = "triggers" not in definition and isinstance(
        definition.get("trigger"), dict
    )
    if singular:
        triggers = [definition["trigger"]]
    if not isinstance(triggers, list):
        return None

    if not any(
        isinstance(t, dict) and t.get("type") in LEGACY_TRIGGER_TYPES for t in triggers
    ) and not singular:
        return None

    converted = dict(definition)
    converted.pop("trigger", None)
    converted["triggers"] = [
        _convert(t) if isinstance(t, dict) else t for t in triggers
    ]
    return converted


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, definition FROM automation_workflow_versions "
            "WHERE definition IS NOT NULL"
        )
    ).fetchall()

    rewritten = 0
    for row in rows:
        definition = row.definition
        if isinstance(definition, str):
            try:
                definition = json.loads(definition)
            except ValueError:
                continue
        if not isinstance(definition, dict):
            continue

        converted = _rewrite(definition)
        if converted is None:
            continue

        payload = json.dumps(converted)
        checksum = hashlib.sha256(
            json.dumps(converted, sort_keys=True).encode()
        ).hexdigest()
        bind.execute(
            sa.text(
                "UPDATE automation_workflow_versions "
                "SET definition = CAST(:definition AS JSONB), "
                "    definition_checksum = :checksum "
                "WHERE id = :id"
            ),
            {"definition": payload, "checksum": checksum, "id": row.id},
        )
        rewritten += 1

    logger.info(
        "legacy trigger migration: %s of %s definitions rewritten",
        rewritten,
        len(rows),
    )


def downgrade() -> None:
    """Deliberately a no-op.

    The retired trigger classes no longer exist, so restoring the old JSON would
    produce definitions the current code cannot load — a worse state than the
    one it came from. The read-time converter is still in place, so rolling the
    application back without rolling this back keeps working.
    """
