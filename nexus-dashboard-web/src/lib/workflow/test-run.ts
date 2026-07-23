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
            const detail = waitDetail(node)
            return {
                step: { node_id: node.id, node_type: "wait", summary: "Wait", detail },
                next: node.next_node_id,
            }
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
                    detail: `Retell agent: ${node.retell_agent_id || "(none)"}`,
                },
                next: node.next_node_id,
            }
        case "update_patient_status":
            return {
                step: {
                    node_id: node.id,
                    node_type: "update_patient_status",
                    summary: "Update status",
                    detail: node.status,
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
                    detail: `Simulated branch: ${branch} (${node.rules.length} rule(s), ${node.logic ?? "AND"}).`,
                },
                next: takeTrue ? node.true_next_node_id : node.false_next_node_id,
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
    if (node.delay.delay_type === "duration") {
        return `Wait ${humanizeSeconds(node.delay.duration_seconds)}`
    }
    if (node.delay.delay_type === "appointment_relative") {
        const seconds = node.delay.offset_seconds
        const direction = seconds < 0 ? "before" : "after"
        return `Wait until ${humanizeSeconds(Math.abs(seconds))} ${direction} appointment`
    }
    return `Wait ${node.delay.offset_days} day(s), then send at ${node.delay.time_of_day} local time`
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
