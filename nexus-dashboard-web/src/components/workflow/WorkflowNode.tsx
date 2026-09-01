/**
 * Custom React Flow node renderers for the workflow canvas: a synthetic trigger node
 * and a step node with type-specific icon, label, and one-line summary. Layout is
 * top-to-bottom, so handles sit on the top (target) and bottom (source); condition
 * nodes expose two bottom source handles (`true`/`false`) matching the derived edges.
 */
import { Handle, Position, type NodeProps } from "@xyflow/react"
import { CheckCircle2, CircleDashed, Clock3, XCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { NODE_META, TRIGGER_META } from "@/lib/workflow/catalog"
import { humanizeSeconds } from "@/lib/workflow/test-run"
import type { FlowNode } from "@/lib/workflow/graph"
import type { WorkflowNode as WfNode, WorkflowTrigger } from "@/types/workflow"

const HANDLE = "!h-2 !w-2 !border !border-background !bg-muted-foreground/70"

function truncate(text: string, max = 44): string {
    const t = text.trim()
    if (!t) return ""
    return t.length > max ? `${t.slice(0, max)}…` : t
}

/** One-line summary of a step for the node card. */
function stepSummary(node: WfNode): string {
    switch (node.type) {
        case "wait":
            if (node.wait_for.type === "sms_reply") {
                return `SMS reply · ${humanizeSeconds(node.wait_for.response_window_seconds ?? 259200)} window`
            }
            if (node.wait_for.delay.delay_type === "duration") return humanizeSeconds(node.wait_for.delay.duration_seconds)
            if (node.wait_for.delay.delay_type === "appointment_relative") {
                const direction = node.wait_for.delay.offset_seconds < 0 ? "before" : "after"
                return `${humanizeSeconds(Math.abs(node.wait_for.delay.offset_seconds))} ${direction} appointment`
            }
            return `${node.wait_for.delay.offset_days} day(s) @ ${node.wait_for.delay.time_of_day}`
        case "drip":
            return `${node.batch_size} every ${humanizeSeconds(node.interval_seconds)}`
        case "send_sms":
            return truncate(node.body_template) || "No message yet"
        case "retell_sms_conversation":
            return node.chat_profile_id
                ? "AI SMS agent selected"
                : "No chat profile selected"
        case "send_email":
            return truncate(node.subject_template) || "No subject yet"
        case "send_voice":
            return node.voice_profile_id
                ? "Voice profile selected"
                : node.retell_agent_id
                    ? "Legacy voice agent"
                    : "No profile selected"
        case "update_patient_status":
            return `Internal: ${truncate(node.status, 30)}`
        case "update_appointment":
            return `PMS: ${node.operation}`
        case "update_gotracker_appointment":
            return node.status_id ? `GoTracker StatusId: ${node.status_id}` : "GoTracker writeback"
        case "json_mapper":
            return `${node.mappings.length} mapping(s)`
        case "llm":
            return `${node.source_field} → ${node.output_field}`
        case "condition":
            return `${node.rules.length} rule(s) · ${node.logic ?? "AND"}`
        case "exit":
            return node.outcome ? `Outcome: ${node.outcome}` : "End of sequence"
    }
}

/** One-line summary of the trigger for the trigger card. */
function triggerSummary(t: WorkflowTrigger): string {
    switch (t.type) {
        case "appointment_offset": {
            const h = Math.abs(t.offset_hours)
            return `${h}h ${t.offset_hours < 0 ? "before" : "after"} appointment`
        }
        case "appointment_state_changed":
            return t.flow_states?.length
                ? `Flow: ${t.flow_states.join(", ")}`
                : t.confirmed !== null && t.confirmed !== undefined
                ? `Confirmed: ${t.confirmed ? "yes" : "no"}`
                : t.preconfirmed !== null && t.preconfirmed !== undefined
                    ? `Preconfirmed: ${t.preconfirmed ? "yes" : "no"}`
                    : t.status_ids.length
                        ? `StatusId: ${t.status_ids.join(", ")}`
                        : "Appointment state"
        case "recall_scan":
            return `Every ${t.recall_interval_months} month(s)`
        case "manual":
            return "Manual / bulk enrollment"
        case "bulk_import":
            return "Bulk import"
        case "callback_requested":
            return "Callback request"
        case "patient_status_changed":
            return `Internal status: ${t.statuses.join(", ")}`
        case "sms_reply":
            return t.tokens?.length ? `Matches: ${t.tokens.join(", ")}` : "Any inbound SMS"
    }
}

function issueRing(level?: "error" | "warning" | null): string {
    if (level === "error") return "ring-2 ring-red-400/80 dark:ring-red-500/70"
    if (level === "warning") return "ring-2 ring-amber-400/80 dark:ring-amber-500/70"
    return ""
}

const EXECUTION_STYLE = {
    completed: { label: "Completed", className: "border-emerald-500 ring-2 ring-emerald-500/25", icon: CheckCircle2, text: "text-emerald-700 dark:text-emerald-400" },
    failed: { label: "Failed", className: "border-red-500 ring-2 ring-red-500/25", icon: XCircle, text: "text-red-700 dark:text-red-400" },
    blocked: { label: "Blocked", className: "border-red-500 ring-2 ring-red-500/25", icon: XCircle, text: "text-red-700 dark:text-red-400" },
    waiting: { label: "Waiting", className: "border-amber-500 ring-2 ring-amber-500/25", icon: Clock3, text: "text-amber-700 dark:text-amber-400" },
    running: { label: "Running", className: "border-blue-500 ring-2 ring-blue-500/25", icon: CircleDashed, text: "text-blue-700 dark:text-blue-400" },
    pending: { label: "Pending", className: "border-zinc-400", icon: CircleDashed, text: "text-zinc-600 dark:text-zinc-400" },
    skipped: { label: "Skipped", className: "border-zinc-400", icon: CircleDashed, text: "text-zinc-600 dark:text-zinc-400" },
} as const

export function TriggerNodeCard({ data }: NodeProps<FlowNode>) {
    if (data.kind !== "trigger") return null
    const meta = TRIGGER_META[data.trigger.type]
    const Icon = meta.icon
    return (
        <div className={cn("w-[210px] rounded-lg border border-dashed border-primary/50 bg-primary/5 p-3 shadow-sm", issueRing(data.issueLevel), data.executionStatus && EXECUTION_STYLE[data.executionStatus].className)}>
            <div className="flex items-start gap-2.5">
                <div className="grid size-8 shrink-0 place-items-center rounded-md bg-primary/15 text-primary">
                    <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="text-2xs font-semibold uppercase tracking-wide text-primary/80">Trigger</div>
                    <div className="truncate text-sm font-medium">{meta.label}</div>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">{triggerSummary(data.trigger)}</p>
                </div>
            </div>
            <Handle type="source" position={Position.Bottom} className={HANDLE} />
        </div>
    )
}

export function StepNodeCard({ data, selected }: NodeProps<FlowNode>) {
    if (data.kind !== "step") return null
    const node = data.node
    const meta = NODE_META[node.type]
    const Icon = meta.icon
    const execution = data.executionStatus ? EXECUTION_STYLE[data.executionStatus] : null
    const ExecutionIcon = execution?.icon
    return (
        <div
            className={cn(
                "relative w-[220px] rounded-lg border border-border bg-card shadow-sm transition-shadow hover:shadow-md",
                selected && "ring-2 ring-primary",
                issueRing(data.issueLevel),
                execution?.className,
            )}
        >
            <Handle type="target" position={Position.Top} className={HANDLE} />
            <div className="flex items-start gap-2.5 p-3">
                <div className={cn("grid size-8 shrink-0 place-items-center rounded-md", meta.accent)}>
                    <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-medium">{meta.label}</span>
                        {data.isEntry && (
                            <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-2xs font-medium text-primary">
                                Start
                            </span>
                        )}
                    </div>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">{stepSummary(node)}</p>
                </div>
            </div>

            {execution && ExecutionIcon && (
                <div className={cn("flex h-7 items-center gap-1.5 border-t px-3 text-xs font-medium", execution.text)}>
                    <ExecutionIcon className={cn("h-3.5 w-3.5", data.executionStatus === "running" && "animate-spin")} />
                    <span>{execution.label}</span>
                    {(data.executionAttempts ?? 0) > 1 && (
                        <span className="ml-auto text-muted-foreground">{data.executionAttempts} attempts</span>
                    )}
                </div>
            )}

            {node.type === "condition" ? (
                <>
                    <Handle id="true" type="source" position={Position.Bottom} style={{ left: "38%" }} className={HANDLE} />
                    <Handle id="false" type="source" position={Position.Bottom} style={{ left: "62%" }} className={HANDLE} />
                </>
            ) : node.type !== "exit" ? (
                <Handle type="source" position={Position.Bottom} className={HANDLE} />
            ) : null}
        </div>
    )
}
