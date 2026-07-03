/**
 * Renders validation issues, linked to nodes. Clicking an issue selects its node
 * (opening the config panel).
 *
 * Two sources are shown, visually distinct:
 *  - `issues`         — fast client-side structural checks (lib/workflow/validation).
 *  - `backendIssues`  — authoritative server checks from POST /automation/workflows/validate
 *                       (consent/content-class compliance + schema). These carry a `code`
 *                       and are badged "Server" so compliance guardrails stand apart from
 *                       structural feedback. Backend is authoritative on publish.
 */
import { AlertCircle, AlertTriangle, CheckCircle2, Radio, ShieldAlert } from "lucide-react"
import { cn } from "@/lib/utils"
import { TRIGGER_NODE_ID } from "@/lib/workflow/graph"
import { readinessIssues, type ChannelStatus } from "@/lib/workflow/readiness"
import type { ValidationIssue } from "@/types/workflow"

export interface WorkflowValidationPanelProps {
    issues: ValidationIssue[]
    /** Authoritative server-side issues (compliance + schema), badged distinctly. */
    backendIssues?: ValidationIssue[]
    /**
     * Per-used-channel readiness for the workflow's location (Plan 02 B6). Omit
     * (or pass empty) for institution-level / no-location workflows — no channels
     * to check. Unready channels render as WARNINGs and feed the warning count.
     */
    readiness?: ChannelStatus[]
    onSelectNode: (id: string | null) => void
}

export default function WorkflowValidationPanel({
    issues,
    backendIssues = [],
    readiness = [],
    onSelectNode,
}: WorkflowValidationPanelProps) {
    const readinessWarnings = readinessIssues(readiness)
    const all = [...issues, ...backendIssues, ...readinessWarnings]
    const errors = all.filter((i) => i.severity === "error")
    const warnings = all.filter((i) => i.severity === "warning")
    const showReadiness = readiness.length > 0

    if (all.length === 0 && !showReadiness) {
        return (
            <div className="flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                All checks passed — ready to publish.
            </div>
        )
    }

    return (
        <div className="space-y-3">
            {all.length === 0 ? (
                <div className="flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
                    <CheckCircle2 className="h-4 w-4 shrink-0" />
                    All checks passed — ready to publish.
                </div>
            ) : (
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
            )}

            {issues.length > 0 && (
                <ul className="space-y-1.5">
                    {issues.map((issue, i) => (
                        <IssueRow key={`client-${i}`} issue={issue} onSelectNode={onSelectNode} />
                    ))}
                </ul>
            )}

            {backendIssues.length > 0 && (
                <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5 pt-1 text-[10px] font-semibold uppercase tracking-wide text-violet-600 dark:text-violet-400">
                        <ShieldAlert className="h-3.5 w-3.5" /> Server &amp; compliance checks
                    </div>
                    <ul className="space-y-1.5">
                        {backendIssues.map((issue, i) => (
                            <IssueRow key={`server-${i}`} issue={issue} onSelectNode={onSelectNode} fromServer />
                        ))}
                    </ul>
                </div>
            )}

            {showReadiness && (
                <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5 pt-1 text-[10px] font-semibold uppercase tracking-wide text-sky-600 dark:text-sky-400">
                        <Radio className="h-3.5 w-3.5" /> Channel readiness
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                        {readiness.map((s) => (
                            <span
                                key={s.channel}
                                className={cn(
                                    "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
                                    s.ready
                                        ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300"
                                        : "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-300",
                                )}
                            >
                                {s.ready ? (
                                    <CheckCircle2 className="h-3 w-3" />
                                ) : (
                                    <AlertTriangle className="h-3 w-3" />
                                )}
                                {s.label}
                            </span>
                        ))}
                    </div>
                    {readinessWarnings.length > 0 && (
                        <ul className="space-y-1.5">
                            {readinessWarnings.map((issue, i) => (
                                <IssueRow key={`readiness-${i}`} issue={issue} onSelectNode={onSelectNode} />
                            ))}
                        </ul>
                    )}
                </div>
            )}
        </div>
    )
}

function IssueRow({
    issue,
    onSelectNode,
    fromServer = false,
}: {
    issue: ValidationIssue
    onSelectNode: (id: string | null) => void
    fromServer?: boolean
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
                    fromServer && "ring-1 ring-inset ring-violet-300/60 dark:ring-violet-700/50",
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
                    <span className="flex items-center gap-1.5">
                        <span className="font-medium text-foreground">{issue.message}</span>
                        {fromServer && (
                            <span className="shrink-0 rounded bg-violet-100 px-1 py-0.5 text-[9px] font-semibold uppercase text-violet-700 dark:bg-violet-950/60 dark:text-violet-300">
                                Server
                            </span>
                        )}
                    </span>
                    {issue.fix && <span className="mt-0.5 block text-muted-foreground">{issue.fix}</span>}
                    {issue.code && (
                        <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground">{issue.code}</span>
                    )}
                    {issue.node_id && issue.node_id !== TRIGGER_NODE_ID && (
                        <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground">{issue.node_id}</span>
                    )}
                </span>
            </button>
        </li>
    )
}
