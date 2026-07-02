import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import {
    Megaphone,
    RefreshCcw,
    Pause,
    Play,
    Archive,
    ChevronRight,
    Loader2,
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import {
    listCampaigns,
    pauseCampaign,
    resumeCampaign,
    archiveCampaign,
} from "@/lib/automation-api"
import type { AutomationWorkflow } from "@/types"

const STATUS_STYLES: Record<string, string> = {
    active: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-400 dark:border-emerald-800",
    paused: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-800",
    archived: "bg-zinc-100 text-zinc-500 border-zinc-200 dark:bg-zinc-800/60 dark:text-zinc-400 dark:border-zinc-700",
    draft: "bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-400 dark:border-blue-800",
}

function StatusBadge({ status }: { status: string }) {
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize",
                STATUS_STYLES[status] ?? STATUS_STYLES.draft,
            )}
        >
            {status}
        </span>
    )
}

function TriggerLabel({ triggerType }: { triggerType: string | null }) {
    if (!triggerType) return <span className="text-muted-foreground text-xs">—</span>
    const labels: Record<string, string> = {
        appointment_offset: "Appointment reminder",
        recall_scan: "Recall / reactivation",
        manual: "Manual",
    }
    return (
        <span className="text-xs text-muted-foreground">
            {labels[triggerType] ?? triggerType}
        </span>
    )
}

export default function Campaigns() {
    const [campaigns, setCampaigns] = useState<AutomationWorkflow[]>([])
    const [loading, setLoading] = useState(true)
    const [acting, setActing] = useState<string | null>(null)

    async function refresh() {
        setLoading(true)
        try {
            setCampaigns(await listCampaigns())
        } catch {
            toast.error("Failed to load campaigns")
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => { refresh() }, [])

    async function handlePause(wf: AutomationWorkflow) {
        setActing(wf.id)
        try {
            const updated = await pauseCampaign(wf.id)
            setCampaigns((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
            toast.success(`"${wf.name}" paused`)
        } catch {
            toast.error("Failed to pause campaign")
        } finally {
            setActing(null)
        }
    }

    async function handleResume(wf: AutomationWorkflow) {
        setActing(wf.id)
        try {
            const updated = await resumeCampaign(wf.id)
            setCampaigns((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
            toast.success(`"${wf.name}" resumed`)
        } catch {
            toast.error("Failed to resume campaign")
        } finally {
            setActing(null)
        }
    }

    async function handleArchive(wf: AutomationWorkflow) {
        if (!confirm(`Archive "${wf.name}"? It will stop accepting new enrollments.`)) return
        setActing(wf.id)
        try {
            const updated = await archiveCampaign(wf.id)
            setCampaigns((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
            toast.success(`"${wf.name}" archived`)
        } catch {
            toast.error("Failed to archive campaign")
        } finally {
            setActing(null)
        }
    }

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none">
                <div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" />
            </div>

            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <Megaphone className="h-7 w-7" />
                        Campaigns
                    </h2>
                    <p className="text-muted-foreground mt-1">
                        Automated outreach workflows for appointment reminders and patient recall.
                    </p>
                </div>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={refresh}
                    disabled={loading}
                    className="gap-1.5"
                >
                    <RefreshCcw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                    Refresh
                </Button>
            </div>

            <Card>
                <CardContent className="p-0">
                    {loading ? (
                        <div className="space-y-2 p-4">
                            {Array.from({ length: 4 }).map((_, i) => (
                                <Skeleton key={i} className="h-14 w-full" />
                            ))}
                        </div>
                    ) : campaigns.length === 0 ? (
                        <div className="flex flex-col items-center gap-2 px-4 py-16 text-center text-muted-foreground">
                            <div className="grid size-12 place-items-center rounded-full bg-muted">
                                <Megaphone className="h-6 w-6 opacity-40" />
                            </div>
                            <p className="text-sm font-medium text-foreground/70">No campaigns yet</p>
                            <p className="text-xs">Automation workflows will appear here once created.</p>
                        </div>
                    ) : (
                        <>
                            <div className="grid grid-cols-[1fr_120px_160px_auto] gap-x-4 border-b border-border px-4 py-2">
                                <span className="text-xs font-medium text-muted-foreground">Name</span>
                                <span className="text-xs font-medium text-muted-foreground">Status</span>
                                <span className="text-xs font-medium text-muted-foreground">Trigger</span>
                                <span />
                            </div>
                            <ul className="divide-y divide-border">
                                {campaigns.map((wf) => {
                                    const busy = acting === wf.id
                                    return (
                                        <li
                                            key={wf.id}
                                            className={cn(
                                                "grid grid-cols-[1fr_120px_160px_auto] items-center gap-x-4 px-4 py-3",
                                                wf.status === "archived" && "opacity-60",
                                            )}
                                        >
                                            <Link
                                                to={`/institution-admin/campaigns/${wf.id}`}
                                                className="font-medium text-sm hover:underline truncate"
                                            >
                                                {wf.name}
                                            </Link>
                                            <StatusBadge status={wf.status} />
                                            <TriggerLabel triggerType={wf.trigger_type} />
                                            <div className="flex items-center gap-1">
                                                {wf.status === "active" && (
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-8 w-8"
                                                        disabled={busy}
                                                        onClick={() => handlePause(wf)}
                                                        title="Pause"
                                                    >
                                                        {busy ? (
                                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                        ) : (
                                                            <Pause className="h-3.5 w-3.5" />
                                                        )}
                                                    </Button>
                                                )}
                                                {wf.status === "paused" && (
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-8 w-8"
                                                        disabled={busy}
                                                        onClick={() => handleResume(wf)}
                                                        title="Resume"
                                                    >
                                                        {busy ? (
                                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                        ) : (
                                                            <Play className="h-3.5 w-3.5" />
                                                        )}
                                                    </Button>
                                                )}
                                                {wf.status !== "archived" && (
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-8 w-8"
                                                        disabled={busy}
                                                        onClick={() => handleArchive(wf)}
                                                        title="Archive"
                                                    >
                                                        <Archive className="h-3.5 w-3.5" />
                                                    </Button>
                                                )}
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-8 w-8"
                                                    asChild
                                                >
                                                    <Link to={`/institution-admin/campaigns/${wf.id}`}>
                                                        <ChevronRight className="h-3.5 w-3.5" />
                                                    </Link>
                                                </Button>
                                            </div>
                                        </li>
                                    )
                                })}
                            </ul>
                        </>
                    )}
                </CardContent>
            </Card>
        </div>
    )
}
