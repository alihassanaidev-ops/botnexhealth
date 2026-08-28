/**
 * Client-side dry-run simulation. There is NO backend test-run endpoint
 * (findings.md §3); `/enroll` executes a real run. This walks the graph from the
 * entry node WITHOUT dispatching anything, producing the path a sample contact would
 * take and the messages that would be sent. Pure module.
 */
import type {
    TestRunResult,
    TestRunStep,
    WorkflowDefinition,
    WorkflowNode,
} from "@/types/workflow"
import { renderTemplate } from "./preview"
import { sampleMergeData } from "./merge-fields"

const MAX_STEPS = 50

export interface SimulateOptions {
    /** Sample merge data for rendering messages. */
    data?: Record<string, string>
    /** Per-condition branch decision (nodeId -> take-true). Default: true branch. */
    conditionChoices?: Record<string, boolean>
}

export function simulateRun(
    def: WorkflowDefinition,
    opts: SimulateOptions = {},
): TestRunResult {
    const data = opts.data ?? sampleMergeData()
    const choices = opts.conditionChoices ?? {}
    const byId = new Map(def.nodes.map((n) => [n.id, n]))
    const steps: TestRunStep[] = []

    let currentId: string | undefined = def.entry_node_id
    let outcome: string | null = null
    let truncated = false

    for (let i = 0; i < MAX_STEPS; i += 1) {
        if (!currentId) break
        const node = byId.get(currentId)
        if (!node) {
            steps.push({
                node_id: currentId,
                node_type: "exit",
                summary: "Dead end",
                detail: `Step "${currentId}" does not exist — the sequence stops here.`,
            })
            break
        }

        const step = describe(node, data, choices)
        steps.push(step.step)
        if (node.type === "exit") {
            outcome = node.outcome ?? null
            break
        }
        currentId = step.next
        if (i === MAX_STEPS - 1 && currentId) truncated = true
    }

    return { steps, outcome, truncated }
}

function describe(
    node: WorkflowNode,
    data: Record<string, string>,
    choices: Record<string, boolean>,
): { step: TestRunStep; next?: string } {
    switch (node.type) {
        case "wait": {
            const summary = node.wait_for.type === "sms_reply" ? "Wait for SMS reply" : "Wait"
            const detail = node.wait_for.type === "sms_reply"
                ? `Pause up to ${humanizeSeconds(node.wait_for.response_window_seconds ?? 259200)}`
                : waitDetail(node)
            return {
                step: {
                    node_id: node.id,
                    node_type: "wait",
                    summary,
                    detail,
                },
                next: node.next_node_id,
            }
        }
        case "drip":
            return {
                step: {
                    node_id: node.id,
                    node_type: "drip",
                    summary: "Drip",
                    detail: `Release ${node.batch_size} contact(s) every ${humanizeSeconds(node.interval_seconds)}`,
                },
                next: node.next_node_id,
            }
        case "send_sms":
            return {
                step: {
                    node_id: node.id,
                    node_type: "send_sms",
                    summary: "Send SMS",
                    detail: renderTemplate(node.body_template, data),
                },
                next: node.next_node_id,
            }
        case "retell_sms_conversation":
            return {
                step: {
                    node_id: node.id,
                    node_type: "retell_sms_conversation",
                    summary: "Wait for Retell-powered SMS conversation",
                    detail: "Patient and appointment context supplied automatically",
                },
                next: node.next_node_id,
            }
        case "send_email":
            return {
                step: {
                    node_id: node.id,
                    node_type: "send_email",
                    summary: `Send email — ${renderTemplate(node.subject_template, data)}`,
                    detail: renderTemplate(node.body_template, data),
                },
                next: node.next_node_id,
            }
        case "send_voice":
            return {
                step: {
                    node_id: node.id,
                    node_type: "send_voice",
                    summary: "Place AI voice call",
                    detail: node.voice_profile_id
                        ? "Voice profile selected"
                        : node.retell_agent_id
                            ? "Legacy voice agent configured"
                            : "No voice profile selected",
                },
                next: node.next_node_id,
            }
        case "update_patient_status":
            return {
                step: {
                    node_id: node.id,
                    node_type: "update_patient_status",
                    summary: "Update internal status",
                    detail: node.status,
                },
                next: node.next_node_id,
            }
        case "update_appointment":
            return {
                step: {
                    node_id: node.id,
                    node_type: "update_appointment",
                    summary: "Update appointment in PMS",
                    detail: node.operation,
                },
                next: node.next_node_id,
            }
        case "update_gotracker_appointment":
            return {
                step: {
                    node_id: node.id,
                    node_type: "update_gotracker_appointment",
                    summary: "Update GoTracker appointment",
                    detail: node.status_id ? `StatusId ${node.status_id}` : "Writeback configured",
                },
                next: node.next_node_id,
            }
        case "json_mapper":
            return {
                step: {
                    node_id: node.id,
                    node_type: "json_mapper",
                    summary: "Map JSON fields",
                    detail: node.mappings.map((mapping) => mapping.target_field).join(", "),
                },
                next: node.next_node_id,
            }
        case "llm":
            return {
                step: {
                    node_id: node.id,
                    node_type: "llm",
                    summary: `Classify → ${node.output_field}`,
                    detail: `Source: ${node.source_field}`,
                },
                next: node.next_node_id,
            }
        case "condition": {
            const takeTrue = choices[node.id] ?? true
            const branch = takeTrue ? "Yes" : "No"
            return {
                step: {
                    node_id: node.id,
                    node_type: "condition",
                    summary: `Condition → ${branch}`,
                    detail: node.filter
                        ? `Simulated branch: ${branch}.`
                        : `Simulated branch: ${branch} (${(node.rules ?? []).length} rule(s), ${node.logic ?? "AND"}).`,
                },
                next: takeTrue ? node.true_next_node_id : node.false_next_node_id,
            }
        }
        case "switch": {
            // The preview walks the fallback branch: without live context there
            // is nothing to match a case against, and guessing the first case
            // would misrepresent what the run will do.
            const subject = node.subject ? ` on ${node.subject}` : ""
            return {
                step: {
                    node_id: node.id,
                    node_type: "switch",
                    summary: `Switch${subject} → Otherwise`,
                    detail: `${node.cases.length} case(s): ${node.cases.map((c) => c.label).join(", ")}.`,
                },
                next: node.default_next_node_id,
            }
        }
        case "exit":
            return {
                step: {
                    node_id: node.id,
                    node_type: "exit",
                    summary: "Exit",
                    detail: node.outcome ? `Outcome: ${node.outcome}` : "End of sequence",
                },
            }
    }
}

function waitDetail(node: Extract<WorkflowNode, { type: "wait" }>): string {
    if (node.wait_for.type !== "time") return "Wait for event"
    if (node.wait_for.delay.delay_type === "duration") {
        return `Wait ${humanizeSeconds(node.wait_for.delay.duration_seconds)}`
    }
    if (node.wait_for.delay.delay_type === "appointment_relative") {
        const seconds = node.wait_for.delay.offset_seconds
        const direction = seconds < 0 ? "before" : "after"
        return `Wait until ${humanizeSeconds(Math.abs(seconds))} ${direction} appointment`
    }
    return `Wait ${node.wait_for.delay.offset_days} day(s), then send at ${node.wait_for.delay.time_of_day} local time`
}

export function humanizeSeconds(seconds: number): string {
    if (seconds <= 0) return "0 seconds"
    const days = Math.floor(seconds / 86400)
    const hours = Math.floor((seconds % 86400) / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    const parts: string[] = []
    if (days) parts.push(`${days} day${days > 1 ? "s" : ""}`)
    if (hours) parts.push(`${hours} hour${hours > 1 ? "s" : ""}`)
    if (mins) parts.push(`${mins} minute${mins > 1 ? "s" : ""}`)
    return parts.length ? parts.join(", ") : `${seconds} seconds`
}
