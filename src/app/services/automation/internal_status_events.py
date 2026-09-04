"""Emit an :class:`InternalStatusEvent` whenever a tracked status changes.

A session listener rather than a call at each write site. There are thirteen
places that assign one of these fields today — nine of them on
``Contact.lead_status`` alone, spread over six files — and the count only goes
up. Hooking the flush means a new write site cannot forget to emit, which is the
failure mode that would make a campaign quietly stop firing.

Three properties this deliberately preserves from the existing
``PatientWorkflowStatusEvent`` path:

* **The row is written in the same transaction as the change.** If the status
  write rolls back, so does the event.
* **Celery is enqueued after commit, never during the flush.** Enqueueing inside
  a flush would publish a status change that a later rollback un-does.
* **The task re-reads the row by id.** The payload carries an id and nothing
  else, so a stale or forged message cannot invent a transition.

Known gap: this sees ORM mutations, not bulk ``UPDATE`` statements. Nothing
issues one against these fields today; if something starts to, it has to emit
explicitly.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from src.app.models.internal_status_event import InternalStatusEvent

logger = logging.getLogger(__name__)

#: ``model class name -> (attribute, tracked field name, subject type)``.
#:
#: Keyed by class *name* rather than the class itself so this module does not
#: import the model layer at definition time and create an import cycle.
_TRACKED: dict[str, tuple[str, str, str]] = {
    "Call": ("workflow_status_id", "call_workflow_status", "call"),
    "Contact": ("lead_status", "contact_lead_status", "contact"),
    "CampaignStaffHandoff": ("status", "handoff_status", "handoff"),
}

#: Where pending events live between the flush that built them and the commit
#: that publishes them.
_PENDING_KEY = "_pending_internal_status_events"


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_status_label(instance: Any, attribute: str, raw: Any) -> str | None:
    """Human-meaningful status value for the event row.

    ``Call.workflow_status_id`` is a foreign key into a tenant-defined
    vocabulary, and a campaign author picks the *name* ("Completed"), not a
    UUID. Resolve through the loaded relationship when it is available and fall
    back to the raw id — an event carrying an id is still better than no event.
    """
    if attribute == "workflow_status_id":
        related = getattr(instance, "workflow_status", None)
        name = getattr(related, "name", None)
        if name:
            return _as_text(name)
    return _as_text(raw)


def _collect(session: Session) -> None:
    """Turn tracked attribute changes on this flush into event rows."""
    pending: list[InternalStatusEvent] = []

    for instance in session.dirty:
        tracked = _TRACKED.get(type(instance).__name__)
        if tracked is None:
            continue
        attribute, field, subject_type = tracked

        state = inspect(instance)
        history = state.attrs[attribute].history
        if not history.has_changes():
            continue

        new_raw = history.added[0] if history.added else None
        old_raw = history.deleted[0] if history.deleted else None
        new_value = _resolve_status_label(instance, attribute, new_raw)
        # The old value cannot be resolved through the relationship — that now
        # points at the new row — so a workflow-status transition reports the
        # previous id. `from_statuses` is the rarely-used half of the trigger.
        old_value = _as_text(old_raw)

        # Clearing a status is not a transition a campaign can act on, and a
        # no-op write is not a change at all.
        if new_value is None or new_value == old_value:
            continue

        institution_id = _as_text(getattr(instance, "institution_id", None))
        if institution_id is None:
            continue

        contact_id = _as_text(getattr(instance, "contact_id", None))
        if subject_type == "contact":
            contact_id = _as_text(getattr(instance, "id", None))

        pending.append(
            InternalStatusEvent(
                institution_id=institution_id,
                location_id=_as_text(getattr(instance, "location_id", None)),
                contact_id=contact_id,
                field=field,
                subject_type=subject_type,
                subject_id=_as_text(getattr(instance, "id", None)) or "",
                from_status=old_value,
                to_status=new_value,
            )
        )

    if pending:
        session.add_all(pending)
        session.info.setdefault(_PENDING_KEY, []).extend(pending)


def _publish(session: Session) -> None:
    """After commit, hand each event id to the trigger task."""
    events = session.info.pop(_PENDING_KEY, None)
    if not events:
        return

    from src.app.tasks.automation_workflow import trigger_internal_status_workflows

    for row in events:
        try:
            trigger_internal_status_workflows.apply_async(
                kwargs={
                    "institution_id": str(row.institution_id),
                    "status_event_id": str(row.id),
                },
                queue="workflow",
            )
        except Exception:
            # The event row is committed either way. A failed enqueue loses a
            # campaign start, which is bad; raising here would additionally fail
            # the request that changed the status, which is worse.
            logger.exception(
                "internal status enqueue failed event=%s field=%s", row.id, row.field
            )


def register_internal_status_listeners() -> None:
    """Attach the listeners. Idempotent, so repeated calls in tests are safe."""
    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)
    if not event.contains(Session, "after_commit", _after_commit):
        event.listen(Session, "after_commit", _after_commit)


def _before_flush(session: Session, _flush_context: Any, _instances: Any) -> None:
    # Never let this break the write it is observing: a campaign that does not
    # start is recoverable, a status update that 500s is not.
    try:
        _collect(session)
    except Exception:
        logger.exception("internal status collection failed")


def _after_commit(session: Session) -> None:
    try:
        _publish(session)
    except Exception:
        logger.exception("internal status publish failed")
