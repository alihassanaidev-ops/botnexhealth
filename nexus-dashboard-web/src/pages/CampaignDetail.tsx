import { useEffect, useState } from "react"
import { Link, useParams } from "react-router-dom"
import {
    ArrowLeft,
    RefreshCcw,
    Pause,
    Play,
    Archive,
    Loader2,
    ActivitySquare,
} from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import {
    getCampaign,
    listCampaignRuns,
    pauseCampaign,
    resumeCampaign,
    archiveCampaign,
} from "@/lib/automation-api"
import type { AutomationWorkflow, AutomationWorkflowRun } from "@/types"

const WORKFLOW_STATUS_STYLES: Record<string, string> = {
    active: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-400 dark:border-emerald-800",
    paused: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-800",
    archived: "bg-zinc-100 text-zinc-500 border-zinc-200 dark:bg-zinc-800/60 dark:text-zinc-400 dark:border-zinc-700",
    draft: "bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-400 dark:border-blue-800",
}

const RUN_STATUS_STYLES: Record<string, string> = {
    running: "text-blue-600 dark:text-blue-400",
    completed: "text-emerald-600 dark:text-emerald-400",
    cancelled: "text-zinc-500 dark:text-zinc-400",
    failed: "text-red-600 dark:text-red-400",
}

function fmt(iso: string | null): string {
    if (!iso) return "—"
    return new Date(iso).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    })
}

function elapsed(run: AutomationWorkflowRun): string {
    const start = run.started_at ? new Date(run.started_at).getTime() : null
    const end = run.completed_at ? new Date(run.completed_at).getTime() : null
    if (!start) return "—"
    const ms = (end ?? Date.now()) - start
    const s = Math.floor(ms / 1000)
    if (s < 60) return `${s}s`
    const m = Math.floor(s / 60)
    if (m < 60) return `${m}m ${s % 60}s`
    return `${Math.floor(m / 60)}h ${m % 60}m`
}

const TRIGGER_LABELS: Record<string, string> = {
    appointment_offset: "Appointment reminder",
    recall_scan: "Recall / reactivation",
    manual: "Manual",
}

export default function CampaignDetail() {
    const { id } = useParams<{ id: string }>()
    const [campaign, setCampaign] = useState<AutomationWorkflow | null>(null)
    const [runs, setRuns] = useState<AutomationWorkflowRun[]>([])
    const [loading, setLoading] = useState(true)
    const [runsLoading, setRunsLoading] = useState(true)
    const [acting, setActing] = useState(false)

    async function refresh() {
        if (!id) return
        setLoading(true)
        setRunsLoading(true)
        try {
            const [wf, wfRuns] = await Promise.all([getCampaign(id), listCampaignRuns(id)])
            setCampaign(wf)
            setRuns(wfRuns)
        } catch {
            toast.error("Failed to load campaign")
        } finally {
            setLoading(false)
            setRunsLoading(false)
        }
    }

    useEffect(() => { refresh() }, [id])

    async function handlePause() {
        if (!campaign) return
        setActing(true)
        try {
            setCampaign(await pauseCampaign(campaign.id))
            toast.success("Campaign paused")
        } catch {
            toast.error("Failed to pause campaign")
        } finally {
            setActing(false)
        }
    }

    async function handleResume() {
        if (!campaign) return
        setActing(true)
        try {
            setCampaign(await resumeCampaign(campaign.id))
            toast.success("Campaign resumed")
        } catch {
            toast.error("Failed to resume campaign")
        } finally {
            setActing(false)
        }
    }

    async function handleArchive() {
        if (!campaign) return
        if (!confirm(`Archive "${campaign.name}"? It will stop accepting new enrollments.`)) return
        setActing(true)
        try {
            setCampaign(await archiveCampaign(campaign.id))
            toast.success("Campaign archived")
        } catch {
            toast.error("Failed to archive campaign")
        } finally {
            setActing(false)
        }
    }

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none">
                <div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" />
            </div>

            <div className="flex items-center gap-3">
                <Button variant="ghost" size="icon" asChild className="h-8 w-8">
                    <Link to="/institution-admin/campaigns">
                        <ArrowLeft className="h-4 w-4" />
                    </Link>
                </Button>
                <span className="text-sm text-muted-foreground">Campaigns</span>
            </div>

            {loading ? (
                <div className="space-y-3">
                    <Skeleton className="h-9 w-64" />
                    <Skeleton className="h-5 w-40" />
                </div>
            ) : campaign ? (
                <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                        <h2 className="text-3xl font-bold tracking-tight">{campaign.name}</h2>
                        <div className="flex items-center gap-3">
                            <span
                                className={cn(
                                    "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize",
                                    WORKFLOW_STATUS_STYLES[campaign.status] ?? WORKFLOW_STATUS_STYLES.draft,
                                )}
                            >
                                {campaign.status}
                            </span>
                            <span className="text-xs text-muted-foreground">
                                {campaign.trigger_type
                                    ? (TRIGGER_LABELS[campaign.trigger_type] ?? campaign.trigger_type)
                                    : "No trigger"}
                            </span>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={refresh}
                            disabled={loading || acting}
                            className="gap-1.5"
                        >
                            <RefreshCcw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                            Refresh
                        </Button>
                        {campaign.status === "active" && (
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={acting}
                                onClick={handlePause}
                                className="gap-1.5"
                            >
                                {acting ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                    <Pause className="h-3.5 w-3.5" />
                                )}
                                Pause
                            </Button>
                        )}
                        {campaign.status === "paused" && (
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={acting}
                                onClick={handleResume}
                                className="gap-1.5"
                            >
                                {acting ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                    <Play className="h-3.5 w-3.5" />
                                )}
                                Resume
                            </Button>
                        )}
                        {campaign.status !== "archived" && (
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={acting}
                                onClick={handleArchive}
                                className="gap-1.5"
                            >
                                <Archive className="h-3.5 w-3.5" />
                                Archive
                            </Button>
                        )}
                    </div>
                </div>
            ) : (
                <p className="text-muted-foreground text-sm">Campaign not found.</p>
            )}

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base font-semibold">
                        <ActivitySquare className="h-4 w-4" />
                        Enrollments
                        {!runsLoading && (
                            <span className="ml-1 text-xs font-normal text-muted-foreground">
                                ({runs.length})
                            </span>
                        )}
                    </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                    {runsLoading ? (
                        <div className="space-y-2 p-4">
                            {Array.from({ length: 5 }).map((_, i) => (
                                <Skeleton key={i} className="h-10 w-full" />
                            ))}
                        </div>
                    ) : runs.length === 0 ? (
                        <div className="flex flex-col items-center gap-2 px-4 py-12 text-center text-muted-foreground">
                            <div className="grid size-12 place-items-center rounded-full bg-muted">
                                <ActivitySquare className="h-6 w-6 opacity-40" />
                            </div>
                            <p className="text-sm font-medium text-foreground/70">No enrollments yet</p>
                            <p className="text-xs">Patients enrolled in this campaign will appear here.</p>
                        </div>
                    ) : (
                        <>
                            <div className="grid grid-cols-[1fr_100px_100px_140px_140px_80px] gap-x-4 border-b border-border px-4 py-2">
                                <span className="text-xs font-medium text-muted-foreground">Run ID</span>
                                <span className="text-xs font-medium text-muted-foreground">Status</span>
                                <span className="text-xs font-medium text-muted-foreground">Outcome</span>
                                <span className="text-xs font-medium text-muted-foreground">Started</span>
                                <span className="text-xs font-medium text-muted-foreground">Completed</span>
                                <span className="text-xs font-medium text-muted-foreground">Elapsed</span>
                            </div>
                            <ul className="divide-y divide-border">
                                {runs.map((run) => (
                                    <li
                                        key={run.id}
                                        className="grid grid-cols-[1fr_100px_100px_140px_140px_80px] items-center gap-x-4 px-4 py-2.5"
                                    >
                                        <span className="font-mono text-xs text-muted-foreground truncate">
                                            {run.id.slice(0, 8)}…
                                        </span>
                                        <span
                                            className={cn(
                                                "text-xs font-medium capitalize",
                                                RUN_STATUS_STYLES[run.status] ?? "text-foreground",
                                            )}
                                        >
                                            {run.status}
                                        </span>
                                        <span className="text-xs text-muted-foreground capitalize">
                                            {run.outcome ?? "—"}
                                        </span>
                                        <span className="text-xs text-muted-foreground">{fmt(run.started_at)}</span>
                                        <span className="text-xs text-muted-foreground">{fmt(run.completed_at)}</span>
                                        <span className="text-xs text-muted-foreground">{elapsed(run)}</span>
                                    </li>
                                ))}
                            </ul>
                        </>
                    )}
                </CardContent>
            </Card>
        </div>
    )
}
