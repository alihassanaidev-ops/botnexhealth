/**
 * Client-side workflow validation → node-linked `ValidationIssue[]`.
 *
 * Mirrors the backend `WorkflowDefinition.validate_graph_structure`
 * (`definition_schema.py`) — entry exists, forward pointers resolve, condition
 * branches resolve, >= 1 exit — and ADDS richer, node-linked checks the backend's
 * single-string ValueError can't express (duplicate ids, empty required content,
 * out-of-range attempts, unreachable nodes, self-loops, unknown merge tokens).
 *
 * The backend remains authoritative on publish (a 422 is caught and surfaced). This
 * exists to give fast, precise, in-canvas feedback. Pure module.
 */
import type { ValidationIssue, WorkflowDefinition, WorkflowNode } from "@/types/workflow"
import { referencedIds, TRIGGER_NODE_ID } from "./graph"
import { unavailableTokens, unknownTokens } from "./merge-fields"

const HHMM_RE = /^([01]\d|2[0-3]):[0-5]\d$/

export function validateDefinition(def: WorkflowDefinition): ValidationIssue[] {
    const issues: ValidationIssue[] = []
    const ids = def.nodes.map((n) => n.id)
    const idSet = new Set(ids)

    // ---- Graph-level ----
    if (def.nodes.length === 0) {
        issues.push({ node_id: null, severity: "error", message: "Workflow has no steps." })
    }
    if (!def.entry_node_id || !idSet.has(def.entry_node_id)) {
        issues.push({
            node_id: null,
            severity: "error",
            message: "The trigger is not connected to a valid first step.",
            fix: "Set the entry step to an existing node.",
        })
    }
    if (!def.nodes.some((n) => n.type === "exit")) {
        issues.push({
            node_id: null,
            severity: "error",
            message: "Workflow must have at least one Exit step.",
            fix: "Add an Exit step to end the sequence.",
        })
    }

    // Duplicate ids.
    const seen = new Set<string>()
    for (const id of ids) {
        if (seen.has(id)) {
            issues.push({
                node_id: id,
                severity: "error",
                message: `Duplicate step id "${id}".`,
            })
        }
        seen.add(id)
    }

    // ---- Trigger ----
    if (def.trigger.type === "recall_scan" && def.trigger.recall_interval_months < 1) {
        issues.push({
            node_id: TRIGGER_NODE_ID,
            severity: "error",
            message: "Recall interval must be at least 1 month.",
        })
    }

    // ---- Per-node ----
    const refError = (node: WorkflowNode, target: string, label: string) => {
        if (!target) {
            issues.push({
                node_id: node.id,
                severity: "error",
                message: `${label} is not connected to a next step.`,
                fix: "Connect this branch to another step.",
            })
        } else if (!idSet.has(target)) {
            issues.push({
                node_id: node.id,
                severity: "error",
                message: `${label} points to a missing step ("${target}").`,
            })
        }
    }

    for (const node of def.nodes) {
        if (!node.id) {
            issues.push({ node_id: null, severity: "error", message: "A step is missing an id." })
        }
        // Self-loop warning.
        if (referencedIds(node).includes(node.id)) {
            issues.push({
                node_id: node.id,
                severity: "warning",
                message: "This step points back to itself, which can loop indefinitely.",
            })
        }

        switch (node.type) {
            case "wait": {
                refError(node, node.next_node_id, "Wait step")
                if (node.delay.delay_type === "duration") {
                    if (node.delay.duration_seconds < 0) {
                        issues.push({
                            node_id: node.id,
                            severity: "error",
                            message: "Wait duration cannot be negative.",
                        })
                    } else if (node.delay.duration_seconds === 0) {
                        issues.push({
                            node_id: node.id,
                            severity: "warning",
                            message: "Wait duration is zero — the step will not pause.",
                        })
                    }
                } else if (node.delay.delay_type === "calendar" && !HHMM_RE.test(node.delay.time_of_day)) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: `Send time "${node.delay.time_of_day}" is not a valid HH:MM time.`,
                    })
                } else if (
                    node.delay.delay_type === "appointment_relative" &&
                    !Number.isFinite(node.delay.offset_seconds)
                ) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Appointment-relative wait needs a valid offset.",
                    })
                }
                break
            }
            case "send_sms": {
                refError(node, node.next_node_id, "SMS step")
                if (!node.body_template.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "SMS message body is empty.",
                        fix: "Write the text patients will receive.",
                    })
                }
                checkAttempts(node.max_attempts, node.id, issues)
                checkTokens(node.body_template, node.id, def, "sms", issues)
                break
            }
            case "send_email": {
                refError(node, node.next_node_id, "Email step")
                if (!node.subject_template.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Email subject is empty.",
                    })
                }
                if (!node.body_template.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Email body is empty.",
                    })
                }
                checkAttempts(node.max_attempts, node.id, issues)
                checkTokens(node.subject_template, node.id, def, "email", issues)
                checkTokens(node.body_template, node.id, def, "email", issues)
                break
            }
            case "send_voice": {
                refError(node, node.next_node_id, "Voice step")
                if (!node.retell_agent_id.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Voice step has no Retell agent selected.",
                        fix: "Choose the location's outbound voice agent.",
                    })
                }
                checkAttempts(node.max_attempts, node.id, issues)
                break
            }
            case "update_patient_status": {
                refError(node, node.next_node_id, "Status update step")
                if (!node.status.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Status update step has no status.",
                    })
                }
                break
            }
            case "condition": {
                refError(node, node.true_next_node_id, "Condition (Yes branch)")
                refError(node, node.false_next_node_id, "Condition (No branch)")
                if (node.rules.length === 0) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Condition has no rules.",
                    })
                }
                node.rules.forEach((r, i) => {
                    if (!r.field.trim()) {
                        issues.push({
                            node_id: node.id,
                            severity: "error",
                            message: `Condition rule ${i + 1} has no field.`,
                        })
                    }
                })
                break
            }
            case "exit":
                break
        }
    }

    // ---- Reachability (warning) ----
    for (const node of unreachableNodes(def)) {
        issues.push({
            node_id: node,
            severity: "warning",
            message: "This step cannot be reached from the trigger.",
            fix: "Connect a previous step to it or remove it.",
        })
    }

    // Errors first, then warnings — stable within group.
    return issues.sort((a, b) => severityRank(a.severity) - severityRank(b.severity))
}

function severityRank(s: ValidationIssue["severity"]): number {
    return s === "error" ? 0 : 1
}

function checkAttempts(
    max: number | undefined,
    nodeId: string,
    issues: ValidationIssue[],
): void {
    if (max === undefined) return
    if (max < 1 || max > 3) {
        issues.push({
            node_id: nodeId,
            severity: "error",
            message: "Max attempts must be between 1 and 3.",
        })
    }
}

function checkTokens(
    template: string,
    nodeId: string,
    def: WorkflowDefinition,
    channel: "sms" | "email" | "voice",
    issues: ValidationIssue[],
): void {
    const unknown = unknownTokens(template)
    if (unknown.length) {
        issues.push({
            node_id: nodeId,
            severity: "warning",
            message: `Unknown merge field(s): ${unknown.join(", ")}.`,
            fix: "Use a field from the merge-field list, or these will render as placeholders.",
        })
    }
    const unavailable = unavailableTokens(template, {
        triggerType: def.trigger.type,
        channel,
    }).filter((token) => !unknown.includes(token))
    if (unavailable.length) {
        issues.push({
            node_id: nodeId,
            severity: "warning",
            message: `Unavailable merge field(s): ${unavailable.join(", ")}.`,
            fix: "Use fields available for this trigger and channel.",
        })
    }
}

/** Node ids not reachable from `entry_node_id` following forward pointers. */
export function unreachableNodes(def: WorkflowDefinition): string[] {
    const byId = new Map(def.nodes.map((n) => [n.id, n]))
    const reached = new Set<string>()
    const stack: string[] = []
    if (byId.has(def.entry_node_id)) stack.push(def.entry_node_id)
    while (stack.length) {
        const id = stack.pop() as string
        if (reached.has(id)) continue
        reached.add(id)
        const node = byId.get(id)
        if (!node) continue
        for (const t of referencedIds(node)) {
            if (byId.has(t)) stack.push(t)
        }
    }
    return def.nodes.map((n) => n.id).filter((id) => !reached.has(id))
}

/** Convenience: true if there are no error-severity issues. */
export function isPublishable(issues: ValidationIssue[]): boolean {
    return !issues.some((i) => i.severity === "error")
}
