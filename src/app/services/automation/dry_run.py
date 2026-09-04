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
    BookAppointmentNode,
    DripNode,
    ExitNode,
    JsonMapperNode,
    LlmNode,
    RetellSmsConversationNode,
    SendEmailNode,
    SendSmsNode,
    SendVoiceNode,
    SplitNode,
    SwitchNode,
    TimeWaitConfig,
    UpdateAppointmentNode,
    UpdateGoTrackerAppointmentNode,
    UpdatePatientStatusNode,
    WaitNode,
    WorkflowDefinition,
    sms_reply_wait_spec,
)
from src.app.services.automation.event_catalog import sample_context
from src.app.services.automation.trigger_lookup import TRIGGER_EVENT_KEYS
from src.app.services.automation.merge_field_catalog import MERGE_FIELD_CATALOG
from src.app.services.automation.node_registry import capability_for
from src.app.services.automation.step_dispatcher import (
    _assign_context_value,
    _classify_with_label_rules,
    _context_value,
    _metadata_value,
)
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


def _sample_context(extra: dict | None, definition: "WorkflowDefinition | None" = None) -> dict:
    """Sample values so a preview shows what a real run would carry.

    Seeded from the same event catalog the builder offers fields from, so a
    condition on ``appointment.status`` evaluates in the preview instead of
    resolving to nothing and always taking the false branch. Merge-field samples
    are layered underneath for the derived contact/location tokens, which are
    computed rather than read from context. Caller-supplied context wins.
    """
    ctx: dict = {spec.name: spec.sample for spec in MERGE_FIELD_CATALOG}

    for key in _preview_event_keys(definition):
        for path, value in _flatten(sample_context(key)):
            ctx.setdefault(path, value)
        _deep_update(ctx, sample_context(key))

    if extra:
        ctx.update(extra)
    return ctx


def _preview_event_keys(definition: "WorkflowDefinition | None") -> list[str]:
    """Events this definition could start from, for seeding the preview."""
    if definition is None:
        return []
    keys: list[str] = []
    for trigger in definition.triggers:
        for key in getattr(trigger, "event_keys", None) or ():
            if key not in keys:
                keys.append(key)
        for key in TRIGGER_EVENT_KEYS.get(trigger.type, ()):
            if key not in keys:
                keys.append(key)
    return keys


def _flatten(nested: dict, prefix: str = "") -> list[tuple[str, object]]:
    """Dotted paths for a nested sample, so flat lookups also resolve."""
    out: list[tuple[str, object]] = []
    for key, value in nested.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.extend(_flatten(value, f"{path}."))
        else:
            out.append((path, value))
    return out


def _deep_update(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, dict):
            branch = target.get(key)
            if not isinstance(branch, dict):
                branch = {}
                target[key] = branch
            _deep_update(branch, value)
        else:
            target.setdefault(key, value)


def _describe_wait(node: WaitNode) -> str:
    if not isinstance(node.wait_for, TimeWaitConfig):
        return "Wait for event"
    delay = node.wait_for.delay
    if delay.delay_type == "duration":
        return f"Wait {delay.duration_seconds} seconds"
    if delay.delay_type == "appointment_relative":
        return f"Wait until appointment offset {delay.offset_seconds} seconds"
    return f"Wait until day +{delay.offset_days} at {delay.time_of_day} (local)"


def _describe_drip(node: DripNode) -> str:
    return (
        f"Drip {node.batch_size} contact"
        f"{'' if node.batch_size == 1 else 's'} every {node.interval_seconds} seconds"
    )


def simulate_run(
    definition: WorkflowDefinition,
    *,
    context: dict | None = None,
    condition_choices: dict[str, bool] | None = None,
    switch_case_choices: dict[str, str] | None = None,
) -> DryRunResult:
    """Walk the definition from the entry node, describing each step.

    Conditions follow ``condition_choices[node_id]`` (default True) and switches
    follow ``switch_case_choices[node_id]``, naming the case label to take
    (default: the fallback branch). Bounded by _MAX_STEPS.
    """
    ctx = _sample_context(context, definition)
    choices = condition_choices or {}
    case_choices = switch_case_choices or {}
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
                DryRunStep(
                    node_id=current,
                    node_type="unknown",
                    summary=f"Node '{current}' not found",
                )
            )
            result.outcome = "error"
            break
        capability = capability_for(node)
        if capability is None or not capability.dry_run_supported:
            result.steps.append(
                DryRunStep(
                    node_id=node.id,
                    node_type=node.type,
                    summary=f"Step type '{node.type}' is not supported by dry-run",
                )
            )
            result.outcome = "error"
            break
        steps += 1

        reply_wait = sms_reply_wait_spec(node)
        if reply_wait is not None:
            result.steps.append(
                DryRunStep(
                    node.id,
                    "wait",
                    "Wait for SMS reply",
                    f"Pause up to {reply_wait.response_window_seconds} seconds",
                )
            )
            current = node.next_node_id
        elif isinstance(node, WaitNode):
            result.steps.append(DryRunStep(node.id, "wait", _describe_wait(node)))
            current = node.next_node_id
        elif isinstance(node, DripNode):
            result.steps.append(DryRunStep(node.id, "drip", _describe_drip(node)))
            current = node.next_node_id
        elif isinstance(node, SendSmsNode):
            body = render_sms_body(node.body_template, None, None, ctx)
            result.steps.append(DryRunStep(node.id, "send_sms", "Send SMS", body))
            current = node.next_node_id
        elif isinstance(node, RetellSmsConversationNode):
            result.steps.append(
                DryRunStep(
                    node.id,
                    node.type,
                    "Wait for Retell-powered SMS conversation",
                    "Patient and appointment context supplied automatically",
                )
            )
            current = node.next_node_id
        elif isinstance(node, SendEmailNode):
            if node.template_key:
                # The dry run is synchronous and has no session, so a saved
                # template's content isn't loaded here — name it instead of
                # rendering an empty body.
                result.steps.append(
                    DryRunStep(
                        node.id,
                        "send_email",
                        f"Send email — saved template '{node.template_key}'",
                        "",
                    )
                )
            else:
                subject = render_sms_body(node.subject_template, None, None, ctx)
                body = render_sms_body(node.body_template, None, None, ctx)
                result.steps.append(
                    DryRunStep(node.id, "send_email", f"Send email — {subject}", body)
                )
            current = node.next_node_id
        elif isinstance(node, SendVoiceNode):
            result.steps.append(
                DryRunStep(
                    node.id,
                    "send_voice",
                    "Place AI voice call",
                    f"agent {node.retell_agent_id}",
                )
            )
            current = node.next_node_id
        elif isinstance(node, UpdatePatientStatusNode):
            result.steps.append(
                DryRunStep(
                    node.id,
                    node.type,
                    f"Set internal patient status to {node.status}",
                )
            )
            current = node.next_node_id
        elif isinstance(node, UpdateAppointmentNode):
            detail = node.start_time if node.operation == "reschedule" else None
            result.steps.append(
                DryRunStep(
                    node.id,
                    node.type,
                    f"{node.operation.title()} appointment",
                    detail,
                )
            )
            current = node.next_node_id
        elif isinstance(node, BookAppointmentNode):
            result.steps.append(
                DryRunStep(
                    node.id,
                    node.type,
                    "Book appointment",
                    (
                        f"{node.appointment_type_id} with {node.provider_id} "
                        f"at {node.start_time}"
                    ),
                )
            )
            current = node.booked_next_node_id
        elif isinstance(node, JsonMapperNode):
            mapped: dict[str, object] = {}
            for mapping in node.mappings:
                value = _context_value(ctx, mapping.source_path)
                if value is None:
                    value = mapping.default_value
                _assign_context_value(ctx, mapping.target_field, value)
                mapped[mapping.target_field] = _metadata_value(value)
            detail = ", ".join(mapped.keys()) or None
            result.steps.append(
                DryRunStep(node.id, "json_mapper", "Map JSON fields", detail)
            )
            current = node.next_node_id
        elif isinstance(node, LlmNode):
            source_value = _context_value(ctx, node.source_field)
            label = _classify_with_label_rules(node, source_value)
            _assign_context_value(ctx, node.output_field, label)
            result.steps.append(
                DryRunStep(node.id, "llm", f"AI action → {node.output_field}", label)
            )
            current = node.next_node_id
        elif isinstance(node, UpdateGoTrackerAppointmentNode):
            detail = (
                f"StatusId {node.status_id}"
                if node.status_id
                else "Writeback configured"
            )
            result.steps.append(
                DryRunStep(
                    node.id,
                    "update_gotracker_appointment",
                    "Update GoTracker appointment",
                    detail,
                )
            )
            current = node.next_node_id
        elif isinstance(node, ConditionNode):
            branch = choices.get(node.id, True)
            result.steps.append(
                DryRunStep(
                    node.id,
                    "condition",
                    f"Condition → {'Yes' if branch else 'No'} branch",
                )
            )
            current = node.true_next_node_id if branch else node.false_next_node_id
        elif isinstance(node, SwitchNode):
            # `case_choices` names the case to take, so a preview can walk any
            # branch. Unset (or an unknown label) previews the default.
            wanted = case_choices.get(node.id)
            chosen = next((case for case in node.cases if case.label == wanted), None)
            subject = f" on {node.subject}" if node.subject else ""
            result.steps.append(
                DryRunStep(
                    node.id,
                    "switch",
                    f"Switch{subject} → {chosen.label if chosen else 'Default'}",
                    detail=", ".join(case.label for case in node.cases),
                )
            )
            current = chosen.next_node_id if chosen else node.default_next_node_id
        elif isinstance(node, SplitNode):
            # A dry run has no run id, so there is no bucket to hash and nothing
            # honest to "simulate". `case_choices` names the arm to walk (same
            # key the switch preview uses); unset previews the first arm, which
            # is the one the author just wrote.
            wanted = case_choices.get(node.id)
            chosen = next(
                (branch for branch in node.branches if branch.label == wanted),
                node.branches[0],
            )
            subject = f" on {node.subject}" if node.subject else ""
            result.steps.append(
                DryRunStep(
                    node.id,
                    "split",
                    f"Split{subject} → {chosen.label} ({chosen.weight}%)",
                    detail=", ".join(
                        f"{branch.label} {branch.weight}%" for branch in node.branches
                    ),
                )
            )
            current = chosen.next_node_id
        elif isinstance(node, ExitNode):
            result.steps.append(
                DryRunStep(node.id, "exit", f"Exit — {node.outcome or 'done'}")
            )
            result.outcome = node.outcome or "exit"
            current = None
        else:  # pragma: no cover - registry contract tests keep this exhaustive
            result.steps.append(
                DryRunStep(
                    node_id=node.id,
                    node_type=node.type,
                    summary=f"Step type '{node.type}' has no dry-run handler",
                )
            )
            result.outcome = "error"
            break

    return result
