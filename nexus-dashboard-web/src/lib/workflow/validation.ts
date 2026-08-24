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
const PHONE_COUNTRY_REGION_RE = /^[A-Z]{2}$/

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
    if (
        def.trigger.type === "appointment_state_changed" &&
        def.trigger.status_ids.length === 0 &&
        (def.trigger.confirmed === null || def.trigger.confirmed === undefined) &&
        (def.trigger.preconfirmed === null || def.trigger.preconfirmed === undefined) &&
        !(def.trigger.flow_states ?? []).some((state) => state.trim())
    ) {
        issues.push({
            node_id: TRIGGER_NODE_ID,
            severity: "error",
            message: "Appointment state trigger needs at least one matcher.",
        })
    }
    if (
        def.trigger.type === "appointment_state_changed" &&
        def.trigger.max_followup_delay_hours !== null &&
        def.trigger.max_followup_delay_hours !== undefined &&
        (!Number.isInteger(def.trigger.max_followup_delay_hours) ||
            def.trigger.max_followup_delay_hours < 0 ||
            def.trigger.max_followup_delay_hours > 168)
    ) {
        issues.push({
            node_id: TRIGGER_NODE_ID,
            severity: "error",
            message: "Latest follow-up window must be a whole number from 0 to 168 hours.",
        })
    }
    if (def.trigger.type === "patient_status_changed" && def.trigger.statuses.length === 0) {
        issues.push({
            node_id: TRIGGER_NODE_ID,
            severity: "error",
            message: "Internal status trigger needs at least one status.",
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
                if (node.wait_for.type === "sms_reply") {
                    const windowSeconds = node.wait_for.response_window_seconds ?? 259200
                    if (!Number.isFinite(windowSeconds) || windowSeconds < 60 || windowSeconds > 2592000) {
                        issues.push({
                            node_id: node.id,
                            severity: "error",
                            message: "SMS reply response window must be between 60 seconds and 30 days.",
                        })
                    }
                    for (const mapping of node.wait_for.response_mappings ?? []) {
                        if (!mapping.tokens?.length) {
                            issues.push({
                                node_id: node.id,
                                severity: "warning",
                                message: "SMS response mapping has no tokens.",
                            })
                        }
                    }
                } else if (node.wait_for.delay.delay_type === "duration") {
                    if (node.wait_for.delay.duration_seconds < 0) {
                        issues.push({
                            node_id: node.id,
                            severity: "error",
                            message: "Wait duration cannot be negative.",
                        })
                    } else if (node.wait_for.delay.duration_seconds === 0) {
                        issues.push({
                            node_id: node.id,
                            severity: "warning",
                            message: "Wait duration is zero — the step will not pause.",
                        })
                    }
                } else if (node.wait_for.delay.delay_type === "calendar" && !HHMM_RE.test(node.wait_for.delay.time_of_day)) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: `Send time "${node.wait_for.delay.time_of_day}" is not a valid HH:MM time.`,
                    })
                } else if (
                    node.wait_for.delay.delay_type === "appointment_relative" &&
                    !Number.isFinite(node.wait_for.delay.offset_seconds)
                ) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Appointment-relative wait needs a valid offset.",
                    })
                }
                break
            }
            case "drip": {
                refError(node, node.next_node_id, "Drip step")
                if (!Number.isFinite(node.batch_size) || node.batch_size < 1 || node.batch_size > 10000) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Drip batch size must be between 1 and 10,000.",
                    })
                }
                if (!Number.isFinite(node.interval_seconds) || node.interval_seconds < 1) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Drip interval must be at least 1 second.",
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
                if ((node.response_window_seconds ?? 259200) < 60) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "SMS response window must be at least 60 seconds.",
                    })
                }
                for (const mapping of node.response_mappings ?? []) {
                    if (!mapping.tokens?.length) {
                        issues.push({
                            node_id: node.id,
                            severity: "warning",
                            message: "SMS response mapping has no tokens.",
                        })
                    }
                }
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
                if (!node.voice_profile_id && !node.retell_agent_id.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Voice step has no outbound voice profile selected.",
                        fix: "Choose a location outbound voice profile.",
                    })
                }
                if (
                    node.phone_country_code_enabled &&
                    !PHONE_COUNTRY_REGION_RE.test(node.phone_country_region || "")
                ) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Phone country override needs a valid country.",
                        fix: "Choose the country used for local-format patient numbers.",
                    })
                }
                if (!node.phone_country_code_enabled) {
                    issues.push({
                        node_id: node.id,
                        severity: "warning",
                        message: "Phone country override is disabled. Local-format patient numbers will not be called.",
                        fix: "Enable the override if this workflow receives patient numbers without a +country code.",
                    })
                }
                if (
                    node.patient_voice_cooldown_hours !== undefined &&
                    (!Number.isInteger(node.patient_voice_cooldown_hours) ||
                        node.patient_voice_cooldown_hours < 0 ||
                        node.patient_voice_cooldown_hours > 168)
                ) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Voice cooldown must be a whole number from 0 to 168 hours.",
                        fix: "Use 24 for one call per patient per day, or 0 to disable.",
                    })
                }
                checkAttempts(node.max_attempts, node.id, issues)
                break
            }
            case "update_patient_status": {
                refError(node, node.next_node_id, "Internal status update step")
                if (!node.status.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Internal status update step has no status.",
                    })
                }
                break
            }
            case "update_appointment": {
                refError(node, node.next_node_id, "Appointment update step")
                if (node.operation === "reschedule" && !node.start_time?.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "Reschedule needs a new start time.",
                    })
                }
                break
            }
            case "update_gotracker_appointment": {
                refError(node, node.next_node_id, "GoTracker appointment update step")
                const hasWriteback =
                    node.status_id !== null && node.status_id !== undefined ||
                    node.confirmed !== null && node.confirmed !== undefined ||
                    node.preconfirmed !== null && node.preconfirmed !== undefined ||
                    Boolean(node.start_time?.trim()) ||
                    Boolean(node.end_time?.trim()) ||
                    node.duration_min !== null && node.duration_min !== undefined ||
                    Boolean(node.provider_id?.trim()) ||
                    Boolean(node.operatory_id?.trim()) ||
                    Boolean(node.patient_id?.trim()) ||
                    Boolean(node.reason?.trim())
                if (!hasWriteback) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "GoTracker appointment update has no fields selected.",
                    })
                }
                if (node.status_id !== null && node.status_id !== undefined && (node.status_id < 1 || node.status_id > 9)) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "GoTracker status ID must be between 1 and 9.",
                    })
                }
                break
            }
            case "json_mapper": {
                refError(node, node.next_node_id, "JSON Mapper step")
                if (node.mappings.length === 0) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "JSON Mapper has no mappings.",
                    })
                }
                node.mappings.forEach((mapping, i) => {
                    if (!mapping.source_path.trim()) {
                        issues.push({
                            node_id: node.id,
                            severity: "error",
                            message: `JSON mapping ${i + 1} has no source path.`,
                        })
                    }
                    if (!mapping.target_field.trim()) {
                        issues.push({
                            node_id: node.id,
                            severity: "error",
                            message: `JSON mapping ${i + 1} has no target field.`,
                        })
                    }
                })
                break
            }
            case "llm": {
                refError(node, node.next_node_id, "LLM step")
                const outputMode = node.output_mode ?? "label"
                if (!node.source_field.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "LLM step has no source field.",
                    })
                }
                if (!node.output_field.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "LLM step has no output field.",
                    })
                }
                if (!node.prompt_template.trim()) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "LLM step has no prompt.",
                    })
                }
                if (!["label", "text", "json"].includes(outputMode)) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "LLM step has an invalid output mode.",
                    })
                }
                if (outputMode === "label" && (node.labels ?? []).length === 0) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "LLM label mode needs at least one allowed label.",
                    })
                }
                if (
                    node.max_output_tokens !== undefined &&
                    (node.max_output_tokens < 1 || node.max_output_tokens > 4096)
                ) {
                    issues.push({
                        node_id: node.id,
                        severity: "error",
                        message: "LLM max output tokens must be between 1 and 4,096.",
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
