/**
 * Custom React Flow node renderers for the workflow canvas: a synthetic trigger node
 * and a step node with type-specific icon, label, and one-line summary. Layout is
 * left-to-right, so handles sit on the left (target) and right (source); condition
 * nodes expose two source handles (`true`/`false`) matching the derived edges.
 */
import { Handle, Position, type NodeProps } from "@xyflow/react"
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
            return node.delay.delay_type === "duration"
                ? humanizeSeconds(node.delay.duration_seconds)
                : `${node.delay.offset_days} day(s) @ ${node.delay.time_of_day}`
        case "send_sms":
            return truncate(node.body_template) || "No message yet"
        case "send_email":
            return truncate(node.subject_template) || "No subject yet"
        case "send_voice":
            return node.retell_agent_id ? `Agent ${truncate(node.retell_agent_id, 20)}` : "No agent selected"
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
        case "recall_scan":
            return `Every ${t.recall_interval_months} month(s)`
        case "manual":
            return "Manual / bulk enrollment"
        case "bulk_import":
            return "Bulk import"
        case "callback_requested":
            return "Callback request"
    }
}

function issueRing(level?: "error" | "warning" | null): string {
    if (level === "error") return "ring-2 ring-red-400/80 dark:ring-red-500/70"
    if (level === "warning") return "ring-2 ring-amber-400/80 dark:ring-amber-500/70"
    return ""
}

export function TriggerNodeCard({ data }: NodeProps<FlowNode>) {
    if (data.kind !== "trigger") return null
    const meta = TRIGGER_META[data.trigger.type]
    const Icon = meta.icon
    return (
        <div className={cn("w-[210px] rounded-lg border border-dashed border-primary/50 bg-primary/5 p-3 shadow-sm", issueRing(data.issueLevel))}>
            <div className="flex items-start gap-2.5">
                <div className="grid size-8 shrink-0 place-items-center rounded-md bg-primary/15 text-primary">
                    <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-primary/80">Trigger</div>
                    <div className="truncate text-sm font-medium">{meta.label}</div>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">{triggerSummary(data.trigger)}</p>
                </div>
            </div>
            <Handle type="source" position={Position.Right} className={HANDLE} />
        </div>
    )
}

export function StepNodeCard({ data, selected }: NodeProps<FlowNode>) {
    if (data.kind !== "step") return null
    const node = data.node
    const meta = NODE_META[node.type]
    const Icon = meta.icon
    return (
        <div
            className={cn(
                "relative w-[220px] rounded-lg border border-border bg-card shadow-sm transition-shadow hover:shadow-md",
                selected && "ring-2 ring-primary",
                issueRing(data.issueLevel),
            )}
        >
            <Handle type="target" position={Position.Left} className={HANDLE} />
            <div className="flex items-start gap-2.5 p-3">
                <div className={cn("grid size-8 shrink-0 place-items-center rounded-md", meta.accent)}>
                    <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-medium">{meta.label}</span>
                        {data.isEntry && (
                            <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                                Start
                            </span>
                        )}
                    </div>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">{stepSummary(node)}</p>
                </div>
            </div>

            {node.type === "condition" ? (
                <>
                    <Handle id="true" type="source" position={Position.Right} style={{ top: "34%" }} className={HANDLE} />
                    <Handle id="false" type="source" position={Position.Right} style={{ top: "66%" }} className={HANDLE} />
                </>
            ) : node.type !== "exit" ? (
                <Handle type="source" position={Position.Right} className={HANDLE} />
            ) : null}
        </div>
    )
}
