"""Server-side dry-run: simulate a workflow run without persisting or sending.

Mirrors the client ``TestRunResult`` contract (nexus-dashboard-web
``lib/workflow/test-run.ts``) so the builder can preview a run against the
*authoritative* backend definition + merge rendering, rather than a client
reimplementation that can drift. Pure — no DB, no dispatch, no sends.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.app.services.automation.definition_schema import (
    ConditionNode,
    ExitNode,
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    WaitNode,
    WorkflowDefinition,
)
from src.app.services.automation.merge_field_catalog import MERGE_FIELD_CATALOG
from src.app.services.automation.template_renderer import render_sms_body

_MAX_STEPS = 50


@dataclass
class DryRunStep:
    node_id: str
    node_type: str
    summary: str
    detail: str | None = None


@dataclass
class DryRunResult:
    steps: list[DryRunStep] = field(default_factory=list)
    outcome: str | None = None
    truncated: bool = False


def _sample_context(extra: dict | None) -> dict:
    """Sample merge values so previews render realistic copy. Caller-supplied
    context overrides the defaults."""
    ctx = {spec.name: spec.sample for spec in MERGE_FIELD_CATALOG}
    if extra:
        ctx.update(extra)
    return ctx


def _describe_wait(node: WaitNode) -> str:
    delay = node.delay
    if delay.delay_type == "duration":
        return f"Wait {delay.duration_seconds} seconds"
    return f"Wait until day +{delay.offset_days} at {delay.time_of_day} (local)"


def simulate_run(
    definition: WorkflowDefinition,
    *,
    context: dict | None = None,
    condition_choices: dict[str, bool] | None = None,
) -> DryRunResult:
    """Walk the definition from the entry node, describing each step. Conditions
    follow ``condition_choices[node_id]`` (default True). Bounded by _MAX_STEPS."""
    ctx = _sample_context(context)
    choices = condition_choices or {}
    node_map = {n.id: n for n in definition.nodes}
    result = DryRunResult()
    current: str | None = definition.entry_node_id
    steps = 0

    while current is not None:
        if steps >= _MAX_STEPS:
            result.truncated = True
            break
        node = node_map.get(current)
        if node is None:
            result.steps.append(
                DryRunStep(node_id=current, node_type="unknown", summary=f"Node '{current}' not found")
            )
            result.outcome = "error"
            break
        steps += 1

        if isinstance(node, WaitNode):
            result.steps.append(DryRunStep(node.id, "wait", _describe_wait(node)))
            current = node.next_node_id
        elif isinstance(node, SendSmsNode):
            body = render_sms_body(node.body_template, None, None, ctx)
            result.steps.append(DryRunStep(node.id, "send_sms", "Send SMS", body))
            current = node.next_node_id
        elif isinstance(node, SendEmailNode):
            subject = render_sms_body(node.subject_template, None, None, ctx)
            body = render_sms_body(node.body_template, None, None, ctx)
            result.steps.append(
                DryRunStep(node.id, "send_email", f"Send email — {subject}", body)
            )
            current = node.next_node_id
        elif isinstance(node, SendVoiceNode):
            result.steps.append(
                DryRunStep(node.id, "send_voice", "Place AI voice call", f"agent {node.retell_agent_id}")
            )
            current = node.next_node_id
        elif isinstance(node, ConditionNode):
            branch = choices.get(node.id, True)
            result.steps.append(
                DryRunStep(node.id, "condition", f"Condition → {'Yes' if branch else 'No'} branch")
            )
            current = node.true_next_node_id if branch else node.false_next_node_id
        elif isinstance(node, ExitNode):
            result.steps.append(DryRunStep(node.id, "exit", f"Exit — {node.outcome or 'done'}"))
            result.outcome = node.outcome or "exit"
            current = None
        else:  # pragma: no cover - discriminated union is exhaustive
            current = None

    return result
