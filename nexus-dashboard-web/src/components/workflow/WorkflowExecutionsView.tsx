import { useEffect, useMemo, useState } from "react"
import { formatDistanceToNow } from "date-fns"
import { AlertCircle, CheckCircle2, ChevronDown, Clock3, Copy, Loader2, RefreshCw, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas"
import { getRunTimeline, listCampaignRuns } from "@/lib/automation-api"
import { cn } from "@/lib/utils"
import {
    definitionToFlow,
    normalizeDefinition,
    TRIGGER_NODE_ID,
    type ExecutionNodeStatus,
    type FlowNode,
} from "@/lib/workflow/graph"
import type { CampaignRunListItem, RunTimeline, RunTimelineItem } from "@/types"
import type { WorkflowDefinition } from "@/types/workflow"

const STATUS_ICON = {
    completed: CheckCircle2,
    failed: XCircle,
    blocked: AlertCircle,
    waiting: Clock3,
    running: Loader2,
} as const

const STATUS_COLOR: Record<string, string> = {
    completed: "text-emerald-700 dark:text-emerald-400",
    failed: "text-red-700 dark:text-red-400",
    blocked: "text-red-700 dark:text-red-400",
    waiting: "text-amber-700 dark:text-amber-400",
    running: "text-blue-700 dark:text-blue-400",
    pending: "text-zinc-600 dark:text-zinc-400",
    skipped: "text-zinc-600 dark:text-zinc-400",
}

type RunFilter = "all" | "failed" | "waiting" | "completed"
type TechnicalTab = "input" | "output"

interface SummaryField {
    label: string
    value: string
}

function statusIcon(status: string) {
    return STATUS_ICON[status as keyof typeof STATUS_ICON] ?? Clock3
}

function formatJson(value: unknown): string {
    return JSON.stringify(value ?? {}, null, 2)
}

function humanize(value: string): string {
    return value
        .replace(/_/g, " ")
        .replace(/\b\w/g, (character: string) => character.toUpperCase())
        .replace(/\b(Sms|Api|Ai|Llm|Json)\b/g, (term) => term.toUpperCase())
}

function humanizeSummary(value: string): string {
    return value.replace(/\b[a-z0-9]+(?:_[a-z0-9]+)+\b/gi, (match) => match.replace(/_/g, " "))
}

function formatDuration(milliseconds: number | null): string {
    if (milliseconds === null) return "Not recorded"
    if (milliseconds < 1000) return `${milliseconds} ms`
    const seconds = milliseconds / 1000
    return seconds < 60 ? `${seconds.toFixed(seconds >= 10 ? 0 : 1)} sec` : `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} sec`
}

function isInternalField(key: string): boolean {
    return /(^|_)(id|ids|sid|token|tokens|ref)(_|$)/i.test(key)
        || ["source", "current_step_id", "trigger_ref_type", "trigger_type"].includes(key)
}

function collectSummaryFields(
    value: Record<string, unknown>,
    fields: SummaryField[],
    depth = 0,
): void {
    if (depth > 2 || fields.length >= 6) return
    for (const [key, fieldValue] of Object.entries(value)) {
        if (fields.length >= 6 || fieldValue === null || fieldValue === undefined || isInternalField(key)) continue
        if (fieldValue === "[redacted]") continue
        if (typeof fieldValue === "string" || typeof fieldValue === "number" || typeof fieldValue === "boolean") {
            fields.push({
                label: humanize(key),
                value: typeof fieldValue === "string" ? humanizeSummary(fieldValue) : fieldValue === true ? "Yes" : fieldValue === false ? "No" : String(fieldValue),
            })
        } else if (Array.isArray(fieldValue)) {
            const visibleValues = fieldValue.filter((item) => ["string", "number", "boolean"].includes(typeof item) && item !== "[redacted]")
            if (visibleValues.length > 0 && visibleValues.length <= 3) {
                fields.push({ label: humanize(key), value: visibleValues.map(String).join(", ") })
            }
        } else if (typeof fieldValue === "object") {
            collectSummaryFields(fieldValue as Record<string, unknown>, fields, depth + 1)
        }
    }
}

function stepItems(timeline: RunTimeline | null): RunTimelineItem[] {
    return timeline?.items.filter((item) => item.kind === "step_execution" && item.step_id) ?? []
}

interface WorkflowExecutionsViewProps {
    workflowId: string
    initialRunId?: string | null
    onRunSelect?: (runId: string) => void
}

export default function WorkflowExecutionsView({
    workflowId,
    initialRunId,
    onRunSelect,
}: WorkflowExecutionsViewProps) {
    const [runs, setRuns] = useState<CampaignRunListItem[]>([])
    const [selectedRunId, setSelectedRunId] = useState<string | null>(initialRunId ?? null)
    const [timeline, setTimeline] = useState<RunTimeline | null>(null)
    const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
    const [selectedAttemptId, setSelectedAttemptId] = useState<string | null>(null)
    const [filter, setFilter] = useState<RunFilter>("all")
    const [technicalOpen, setTechnicalOpen] = useState(false)
    const [technicalTab, setTechnicalTab] = useState<TechnicalTab>("output")
    const [loadingRuns, setLoadingRuns] = useState(true)
    const [loadingTimeline, setLoadingTimeline] = useState(!!initialRunId)
    const [refreshKey, setRefreshKey] = useState(0)

    useEffect(() => {
        let cancelled = false
        void listCampaignRuns(workflowId, { limit: 50 })
            .then((result) => {
                if (cancelled) return
                setRuns(result.items)
                setSelectedRunId((current) => {
                    if (!current && result.items[0]) setLoadingTimeline(true)
                    return current ?? result.items[0]?.id ?? null
                })
            })
            .catch(() => {
                if (!cancelled) setRuns([])
            })
            .finally(() => {
                if (!cancelled) setLoadingRuns(false)
            })
        return () => { cancelled = true }
    }, [workflowId, refreshKey])

    useEffect(() => {
        if (!selectedRunId) return
        let cancelled = false
        void getRunTimeline(workflowId, selectedRunId)
            .then((result) => {
                if (cancelled) return
                setTimeline(result)
                const steps = stepItems(result)
                const latest = steps[steps.length - 1]
                setSelectedNodeId(latest?.step_id ?? null)
                setSelectedAttemptId(latest?.id ?? null)
            })
            .catch(() => {
                if (!cancelled) setTimeline(null)
            })
            .finally(() => {
                if (!cancelled) setLoadingTimeline(false)
            })
        return () => { cancelled = true }
    }, [workflowId, selectedRunId, refreshKey])

    const filteredRuns = useMemo(
        () => runs.filter((run) => filter === "all" || run.status === filter),
        [runs, filter],
    )

    const executionFlow = useMemo(() => {
        if (!timeline?.workflow_version?.definition) return { nodes: [] as FlowNode[], edges: [] }
        const definition = normalizeDefinition(
            timeline.workflow_version.definition as unknown as WorkflowDefinition,
        )
        const flow = definitionToFlow(definition)
        const steps = stepItems(timeline)
        const attempts = new Map<string, RunTimelineItem[]>()
        for (const item of steps) {
            const list = attempts.get(item.step_id as string) ?? []
            list.push(item)
            attempts.set(item.step_id as string, list)
        }
        const pathPairs = new Set<string>()
        if (steps[0]?.step_id) pathPairs.add(`${TRIGGER_NODE_ID}:${steps[0].step_id}`)
        for (let index = 1; index < steps.length; index += 1) {
            const previous = steps[index - 1].step_id
            const current = steps[index].step_id
            if (previous && current && previous !== current) pathPairs.add(`${previous}:${current}`)
        }
        const nodes = flow.nodes.map((node): FlowNode => {
            if (node.data.kind === "trigger") {
                return {
                    ...node,
                    data: { ...node.data, executionStatus: steps.length ? "completed" : undefined },
                }
            }
            const nodeAttempts = attempts.get(node.id) ?? []
            const latest = nodeAttempts[nodeAttempts.length - 1]
            return {
                ...node,
                data: {
                    ...node.data,
                    executionStatus: latest?.status as ExecutionNodeStatus | undefined,
                    executionAttempts: nodeAttempts.length,
                },
            }
        })
        const edges = flow.edges.map((edge) => {
            const traversed = pathPairs.has(`${edge.source}:${edge.target}`)
            return traversed
                ? { ...edge, animated: timeline.run.status === "running", style: { ...edge.style, stroke: "#10b981", strokeWidth: 3, strokeOpacity: 1 } }
                : { ...edge, style: { ...edge.style, strokeOpacity: 0.2 } }
        })
        return { nodes, edges }
    }, [timeline])

    const selectedAttempts = useMemo(
        () => stepItems(timeline).filter((item) => item.step_id === selectedNodeId),
        [timeline, selectedNodeId],
    )
    const selectedAttempt = selectedAttempts.find((item) => item.id === selectedAttemptId)
        ?? selectedAttempts[selectedAttempts.length - 1]
        ?? null
    const summaryFields = useMemo(() => {
        if (!selectedAttempt) return []
        const fields: SummaryField[] = []
        collectSummaryFields(selectedAttempt.output, fields)
        collectSummaryFields(selectedAttempt.input, fields)
        const summary = humanizeSummary(selectedAttempt.summary ?? "").toLowerCase()
        return fields.filter(
            (field, index) =>
                !summary.includes(field.value.toLowerCase())
                && fields.findIndex((candidate) => candidate.label === field.label && candidate.value === field.value) === index,
        )
    }, [selectedAttempt])

    function selectNode(nodeId: string | null) {
        if (!nodeId || nodeId === TRIGGER_NODE_ID) return
        setSelectedNodeId(nodeId)
        setSelectedAttemptId(null)
        setTechnicalOpen(false)
        setTechnicalTab("output")
    }

    function selectRun(runId: string) {
        setLoadingTimeline(true)
        setTimeline(null)
        setSelectedRunId(runId)
        setTechnicalOpen(false)
        setTechnicalTab("output")
        onRunSelect?.(runId)
    }

    function refresh() {
        setLoadingRuns(true)
        setLoadingTimeline(!!selectedRunId)
        setRefreshKey((key) => key + 1)
    }

    return (
        <div className="flex min-h-0 flex-1">
            <aside className="flex w-72 shrink-0 flex-col border-r border-border bg-background">
                <div className="flex items-center gap-1 border-b p-2">
                    {(["all", "failed", "waiting", "completed"] as RunFilter[]).map((value) => (
                        <button
                            key={value}
                            type="button"
                            onClick={() => setFilter(value)}
                            className={cn("rounded px-2 py-1 text-xs capitalize", filter === value ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted")}
                        >
                            {value}
                        </button>
                    ))}
                    <Button variant="ghost" size="icon" className="ml-auto h-7 w-7" onClick={refresh} title="Refresh executions">
                        <RefreshCw className="h-3.5 w-3.5" />
                    </Button>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto">
                    {loadingRuns && <Loader2 className="mx-auto mt-8 h-5 w-5 animate-spin text-muted-foreground" />}
                    {!loadingRuns && filteredRuns.length === 0 && <p className="p-4 text-sm text-muted-foreground">No executions found.</p>}
                    {filteredRuns.map((run) => {
                        const Icon = statusIcon(run.status)
                        return (
                            <button
                                key={run.id}
                                type="button"
                                onClick={() => selectRun(run.id)}
                                className={cn("w-full border-b px-3 py-3 text-left hover:bg-muted/60", selectedRunId === run.id && "bg-muted")}
                            >
                                <div className="flex items-center gap-2">
                                    <Icon className={cn("h-4 w-4 shrink-0", STATUS_COLOR[run.status], run.status === "running" && "animate-spin")} />
                                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{run.contact_name || "Unknown contact"}</span>
                                    <span className={cn("text-xs capitalize", STATUS_COLOR[run.status])}>{run.status}</span>
                                </div>
                                <div className="mt-1 flex justify-between pl-6 text-xs text-muted-foreground">
                                    <span className="truncate">{run.current_step_type?.split("_").join(" ") || run.outcome || "Started"}</span>
                                    <span className="shrink-0">{formatDistanceToNow(new Date(run.latest_event_at || run.created_at), { addSuffix: true })}</span>
                                </div>
                            </button>
                        )
                    })}
                </div>
            </aside>

            <div className="relative min-h-0 flex-1">
                {loadingTimeline ? (
                    <div
                        role="status"
                        aria-label="Loading execution"
                        className="absolute inset-0 grid place-items-center"
                    >
                        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                ) : timeline ? (
                    <WorkflowCanvas
                        nodes={executionFlow.nodes}
                        edges={executionFlow.edges}
                        selectedId={selectedNodeId}
                        onSelect={selectNode}
                    />
                ) : (
                    <p className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-sm text-muted-foreground">Select an execution.</p>
                )}
                {timeline && (
                    <div className="absolute left-3 top-3 rounded-md border bg-background/95 px-3 py-2 shadow-sm">
                        <div className="text-xs font-medium">Version {timeline.workflow_version.version_number}</div>
                        <div className="text-[11px] text-muted-foreground">Run {timeline.run.id.slice(0, 8)}</div>
                    </div>
                )}
            </div>

            <aside className="w-80 shrink-0 overflow-y-auto border-l border-border bg-background">
                {selectedAttempt ? (
                    <div className="flex min-h-full flex-col">
                        <div className="border-b p-4">
                            <div className="flex items-center justify-between gap-2">
                                <h3 className="truncate text-sm font-semibold">{selectedAttempt.title}</h3>
                                <span className={cn("text-xs font-medium capitalize", STATUS_COLOR[selectedAttempt.status || "pending"])}>{selectedAttempt.status}</span>
                            </div>
                            <div className="mt-1 flex items-center justify-between gap-3 text-xs text-muted-foreground">
                                <span>Attempt {String(selectedAttempt.metadata.attempt_number ?? 1)}{selectedAttempts.length > 1 ? ` of ${selectedAttempts.length}` : ""}</span>
                                <span>{formatDuration(selectedAttempt.duration_ms)}</span>
                            </div>
                        </div>

                        <div className="space-y-5 p-4">
                            <section>
                                <h4 className="text-xs font-semibold uppercase text-muted-foreground">Summary</h4>
                                {selectedAttempt.summary ? (
                                    <p className="mt-2 text-sm leading-5 text-foreground">{humanizeSummary(selectedAttempt.summary)}</p>
                                ) : (
                                    <p className="mt-2 text-sm text-muted-foreground">This step did not record a result summary.</p>
                                )}
                                <dl className="mt-3 divide-y divide-border border-y text-xs">
                                    <div className="flex items-center justify-between gap-3 py-2">
                                        <dt className="text-muted-foreground">Action</dt>
                                        <dd className="text-right font-medium">{humanize(String(selectedAttempt.node.type ?? selectedAttempt.step_id ?? "Workflow step"))}</dd>
                                    </div>
                                    {selectedAttempt.channel && (
                                        <div className="flex items-center justify-between gap-3 py-2">
                                            <dt className="text-muted-foreground">Channel</dt>
                                            <dd className="text-right font-medium">{humanize(selectedAttempt.channel)}</dd>
                                        </div>
                                    )}
                                    <div className="flex items-center justify-between gap-3 py-2">
                                        <dt className="text-muted-foreground">Started</dt>
                                        <dd className="text-right font-medium">{new Date(selectedAttempt.occurred_at).toLocaleString()}</dd>
                                    </div>
                                    <div className="flex items-center justify-between gap-3 py-2">
                                        <dt className="text-muted-foreground">Duration</dt>
                                        <dd className="text-right font-medium">{formatDuration(selectedAttempt.duration_ms)}</dd>
                                    </div>
                                </dl>
                            </section>

                            {selectedAttempt.error_message && (
                                <section className="rounded-md border border-red-500/30 bg-red-500/5 p-3">
                                    <div className="flex items-start gap-2">
                                        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-600 dark:text-red-400" />
                                        <div>
                                            <h4 className="text-xs font-semibold text-red-700 dark:text-red-400">Why it failed</h4>
                                            <p className="mt-1 text-xs leading-5 text-foreground">{selectedAttempt.error_message}</p>
                                            <p className="mt-2 text-xs leading-5 text-muted-foreground">Review this step&apos;s configuration and provider response before retrying.</p>
                                        </div>
                                    </div>
                                </section>
                            )}

                            {summaryFields.length > 0 && (
                                <section>
                                    <h4 className="text-xs font-semibold uppercase text-muted-foreground">Execution details</h4>
                                    <dl className="mt-2 divide-y divide-border border-y text-xs">
                                        {summaryFields.map((field) => (
                                            <div key={`${field.label}:${field.value}`} className="flex items-start justify-between gap-3 py-2">
                                                <dt className="text-muted-foreground">{field.label}</dt>
                                                <dd className="max-w-[55%] break-words text-right font-medium">{field.value}</dd>
                                            </div>
                                        ))}
                                    </dl>
                                </section>
                            )}

                            {selectedAttempts.length > 1 && (
                                <section>
                                    <h4 className="text-xs font-semibold uppercase text-muted-foreground">Attempts ({selectedAttempts.length})</h4>
                                    <div className="mt-2 divide-y divide-border border-y">
                                        {selectedAttempts.map((attempt, index) => (
                                            <button
                                                key={attempt.id}
                                                type="button"
                                                className={cn("w-full px-1 py-2.5 text-left text-xs hover:bg-muted/60", selectedAttempt.id === attempt.id && "bg-muted")}
                                                onClick={() => {
                                                    setSelectedAttemptId(attempt.id)
                                                    setTechnicalOpen(false)
                                                    setTechnicalTab("output")
                                                }}
                                            >
                                                <div className="flex items-center justify-between gap-2">
                                                    <span className="font-medium">Attempt {index + 1}</span>
                                                    <span className={cn("font-medium capitalize", STATUS_COLOR[attempt.status || "pending"])}>{attempt.status}</span>
                                                </div>
                                                <div className="mt-1 flex items-center justify-between gap-2 text-muted-foreground">
                                                    <span>{new Date(attempt.occurred_at).toLocaleString()}</span>
                                                    <span>{formatDuration(attempt.duration_ms)}</span>
                                                </div>
                                            </button>
                                        ))}
                                    </div>
                                </section>
                            )}
                        </div>

                        <div className="mt-auto border-t border-border">
                            <button
                                type="button"
                                className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-xs font-medium hover:bg-muted/60"
                                aria-expanded={technicalOpen}
                                onClick={() => setTechnicalOpen((open) => !open)}
                            >
                                Technical details
                                <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform", technicalOpen && "rotate-180")} />
                            </button>
                            {technicalOpen && (
                                <div className="border-t border-border">
                                    <div className="flex items-center gap-1 p-2">
                                        {(["output", "input"] as TechnicalTab[]).map((tab) => (
                                            <button
                                                key={tab}
                                                type="button"
                                                onClick={() => setTechnicalTab(tab)}
                                                className={cn("rounded px-2.5 py-1.5 text-xs font-medium capitalize", technicalTab === tab ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted/60")}
                                            >
                                                {tab}
                                            </button>
                                        ))}
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            className="ml-auto h-7 w-7"
                                            aria-label="Copy technical JSON"
                                            title="Copy technical JSON"
                                            onClick={() => void navigator.clipboard?.writeText(formatJson(technicalTab === "input" ? selectedAttempt.input : selectedAttempt.output))}
                                        >
                                            <Copy className="h-3.5 w-3.5" />
                                        </Button>
                                    </div>
                                    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border-t border-border p-4 text-[11px] leading-5 text-muted-foreground">{formatJson(technicalTab === "input" ? selectedAttempt.input : selectedAttempt.output)}</pre>
                                </div>
                            )}
                        </div>
                    </div>
                ) : (
                    <p className="p-4 text-sm text-muted-foreground">Select an executed node.</p>
                )}
            </aside>
        </div>
    )
}
