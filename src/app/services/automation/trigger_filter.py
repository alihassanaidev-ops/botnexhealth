"""Trigger-level eligibility, evaluated before a run exists.

Campaigns used to decide eligibility inside the graph: both live templates open
with a condition node whose false branch is ``exit-ineligible-reason``. That
works, but it means every appointment event in the clinic writes a run row, a
step-execution row and analytics rows before exiting at node one — the write
volume is proportional to *all* events rather than eligible ones.

A trigger's optional ``filter`` moves that decision in front of enrollment. The
opening condition node becomes unnecessary, and an ineligible subject costs one
in-memory evaluation instead of several rows.

Failure posture: a filter that cannot be evaluated is treated as **not
matching**. An author who wrote a filter meant to narrow the audience, so the
safe direction on malformed input is to contact fewer people, not more.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from src.app.models.automation_workflow import AutomationWorkflow
from src.app.services.automation.definition_schema import WorkflowDefinition
from src.app.services.automation.filter_expression import (
    EvaluationContext,
    evaluate,
)

logger = logging.getLogger(__name__)


def trigger_filter_matches(
    workflow: AutomationWorkflow,
    context: Mapping[str, Any],
    *,
    location_timezone: str = "UTC",
    now: datetime | None = None,
) -> bool:
    """Whether this workflow's trigger filter admits the event.

    Returns ``True`` when the trigger declares no filter, which is every
    definition published before filters existed.
    """
    definition = _definition_for(workflow)
    if definition is None:
        # A workflow whose definition will not parse cannot be matched against;
        # the caller's own trigger-type check already excluded it in practice.
        return False

    expression = getattr(definition.trigger, "filter", None)
    if expression is None:
        return True

    try:
        return evaluate(
            expression,
            EvaluationContext(
                values=context,
                now=now or datetime.now(tz=timezone.utc),
                timezone_name=location_timezone,
            ),
        )
    except Exception:  # noqa: BLE001 — a bad filter must not abort the sweep
        logger.exception(
            "trigger_filter: evaluation failed workflow=%s; treating as no match",
            workflow.id,
        )
        return False


def _definition_for(workflow: AutomationWorkflow) -> WorkflowDefinition | None:
    if not workflow.definition:
        return None
    try:
        return WorkflowDefinition.model_validate(workflow.definition)
    except Exception:  # noqa: BLE001
        return None
