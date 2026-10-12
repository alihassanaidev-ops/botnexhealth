"""Which appointment types a live recall campaign call may see and book.

A recall campaign sets ``allowed_appointment_type_ids`` on its PMS recall
source. The outbound voice step records the Retell call id on its voice
attempt, so from the call id this resolves the campaign run, its immutable
workflow version, and that list. The Retell tools then hide every other type
and refuse to search or book one.

A call that is not a recall campaign call, or a recall campaign with an empty
list, resolves to ``None`` and is left untouched, so inbound calls and other
campaigns keep every appointment type.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from src.app.models.automation_workflow import (
    AutomationWorkflowRun,
    AutomationWorkflowVersion,
)
from src.app.models.outbound_voice import WorkflowVoiceAttempt
from src.app.services.automation.definition_schema import (
    PmsRecallSource,
    ScheduleTrigger,
    WorkflowDefinition,
)

logger = logging.getLogger(__name__)

_SOURCE_PREFIXES = ("nh-", "gt-")


def normalize_appointment_type_id(value: Any) -> str | None:
    """Compare ids with or without the ``nh-``/``gt-`` source prefix."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    for prefix in _SOURCE_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text or None


def appointment_type_allowed(allowed: frozenset[str], type_id: Any) -> bool:
    normalized = normalize_appointment_type_id(type_id)
    return normalized is not None and normalized in allowed


def _recall_source(definition: WorkflowDefinition) -> PmsRecallSource | None:
    for trigger in definition.triggers:
        if isinstance(trigger, ScheduleTrigger) and isinstance(
            trigger.source, PmsRecallSource
        ):
            return trigger.source
    return None


async def resolve_recall_appointment_types_for_call(
    session, call_id: str | None
) -> frozenset[str] | None:
    """The allowed type ids for a recall campaign call, else ``None``."""
    if not call_id:
        return None
    attempt = (
        await session.execute(
            select(WorkflowVoiceAttempt.workflow_run_id)
            .where(WorkflowVoiceAttempt.retell_call_id == str(call_id))
            .limit(1)
        )
    ).first()
    if attempt is None:
        return None
    run = await session.get(AutomationWorkflowRun, attempt[0])
    if run is None or not run.workflow_version_id:
        return None
    version = await session.get(AutomationWorkflowVersion, run.workflow_version_id)
    if version is None or not version.definition:
        return None
    try:
        definition = WorkflowDefinition.model_validate(version.definition)
    except Exception:  # noqa: BLE001
        logger.warning("recall guard: unreadable definition run=%s", attempt[0])
        return None
    source = _recall_source(definition)
    if source is None:
        return None
    allowed = frozenset(
        value
        for value in (
            normalize_appointment_type_id(item)
            for item in source.allowed_appointment_type_ids
        )
        if value
    )
    return allowed or None
