/**
 * Renders client-side validation issues, linked to nodes. Clicking an issue selects
 * its node (opening the config panel). Backend validation remains authoritative on
 * publish; this is fast in-canvas feedback.
 */
import { AlertCircle, AlertTriangle, CheckCircle2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { TRIGGER_NODE_ID } from "@/lib/workflow/graph"
import type { ValidationIssue } from "@/types/workflow"

export interface WorkflowValidationPanelProps {
    issues: ValidationIssue[]
    onSelectNode: (id: string | null) => void
}

export default function WorkflowValidationPanel({ issues, onSelectNode }: WorkflowValidationPanelProps) {
    const errors = issues.filter((i) => i.severity === "error")
    const warnings = issues.filter((i) => i.severity === "warning")

    if (issues.length === 0) {
        return (
            <div className="flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                All checks passed — ready to publish.
            </div>
        )
    }

    return (
        <div className="space-y-2">
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
                {errors.length > 0 && (
                    <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400">
                        <AlertCircle className="h-3.5 w-3.5" /> {errors.length} error{errors.length > 1 ? "s" : ""}
                    </span>
                )}
                {warnings.length > 0 && (
                    <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
                        <AlertTriangle className="h-3.5 w-3.5" /> {warnings.length} warning{warnings.length > 1 ? "s" : ""}
                    </span>
                )}
            </div>
            <ul className="space-y-1.5">
                {issues.map((issue, i) => (
                    <IssueRow key={i} issue={issue} onSelectNode={onSelectNode} />
                ))}
            </ul>
        </div>
    )
}

function IssueRow({
    issue,
    onSelectNode,
}: {
    issue: ValidationIssue
    onSelectNode: (id: string | null) => void
}) {
    const isError = issue.severity === "error"
    const Icon = isError ? AlertCircle : AlertTriangle
    const clickable = issue.node_id !== null
    const target = issue.node_id === TRIGGER_NODE_ID ? TRIGGER_NODE_ID : issue.node_id

    return (
        <li>
            <button
                type="button"
                disabled={!clickable}
                onClick={() => clickable && onSelectNode(target)}
                className={cn(
                    "flex w-full items-start gap-2 rounded-md border px-2.5 py-2 text-left text-xs transition-colors",
                    isError
                        ? "border-red-200 bg-red-50 dark:border-red-900/60 dark:bg-red-950/30"
                        : "border-amber-200 bg-amber-50 dark:border-amber-900/60 dark:bg-amber-950/30",
                    clickable && "hover:brightness-95 dark:hover:brightness-125",
                )}
            >
                <Icon
                    className={cn(
                        "mt-0.5 h-3.5 w-3.5 shrink-0",
                        isError ? "text-red-600 dark:text-red-400" : "text-amber-600 dark:text-amber-400",
                    )}
                />
                <span className="min-w-0">
                    <span className="font-medium text-foreground">{issue.message}</span>
                    {issue.fix && <span className="mt-0.5 block text-muted-foreground">{issue.fix}</span>}
                    {issue.node_id && issue.node_id !== TRIGGER_NODE_ID && (
                        <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground">{issue.node_id}</span>
                    )}
                </span>
            </button>
        </li>
    )
}
