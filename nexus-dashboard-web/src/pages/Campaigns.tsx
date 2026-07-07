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
    Plus,
    Workflow,
    ShieldAlert,
    ShieldCheck,
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import {
    activateOutboundHalt,
    listCampaigns,
    getOutboundHaltStatus,
    pauseCampaign,
    releaseOutboundHalt,
    resumeCampaign,
    archiveCampaign,
} from "@/lib/automation-api"
import type { AutomationWorkflow, OutboundHaltStatus } from "@/types"

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
    const [haltStatus, setHaltStatus] = useState<OutboundHaltStatus | null>(null)
    const [loading, setLoading] = useState(true)
    const [acting, setActing] = useState<string | null>(null)
    const [archiveTarget, setArchiveTarget] = useState<AutomationWorkflow | null>(null)
    const [haltDialog, setHaltDialog] = useState<"activate" | "release" | null>(null)

    async function refresh() {
        setLoading(true)
        try {
            const [nextCampaigns, nextHalt] = await Promise.all([
                listCampaigns(),
                getOutboundHaltStatus(),
            ])
            setCampaigns(nextCampaigns)
            setHaltStatus(nextHalt)
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
        setActing(wf.id)
        try {
            const updated = await archiveCampaign(wf.id)
            setCampaigns((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
            toast.success(`"${wf.name}" archived`)
            setArchiveTarget(null)
        } catch {
            toast.error("Failed to archive campaign")
        } finally {
            setActing(null)
        }
    }

    async function handleActivateHalt() {
        setActing("outbound-halt")
        try {
            const next = await activateOutboundHalt("Activated from campaign management")
            setHaltStatus(next)
            const suffix = next.halted_runs ? ` ${next.halted_runs} runs stopped.` : ""
            toast.success(`Outbound halt activated.${suffix}`)
            setHaltDialog(null)
        } catch {
            toast.error("Failed to activate outbound halt")
        } finally {
            setActing(null)
        }
    }

    async function handleReleaseHalt() {
        setActing("outbound-halt")
        try {
            setHaltStatus(await releaseOutboundHalt())
            toast.success("Outbound halt released")
            setHaltDialog(null)
        } catch {
            toast.error("Failed to release outbound halt")
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
                <div className="flex items-center gap-2">
                    <Button
                        variant={haltStatus?.halted ? "default" : "outline"}
                        size="sm"
                        onClick={() => setHaltDialog(haltStatus?.halted ? "release" : "activate")}
                        disabled={loading || acting === "outbound-halt"}
                        className="gap-1.5"
                    >
                        {haltStatus?.halted ? (
                            <ShieldAlert className="h-3.5 w-3.5" />
                        ) : (
                            <ShieldCheck className="h-3.5 w-3.5" />
                        )}
                        {haltStatus?.halted ? "Outbound halted" : "Outbound clear"}
                    </Button>
                    <Button size="sm" asChild className="gap-1.5">
                        <Link to="/institution-admin/campaigns/templates">
                            <Plus className="h-3.5 w-3.5" />
                            New from template
                        </Link>
                    </Button>
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
            </div>

            {haltStatus?.halted && (
                <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
                    <div className="flex items-start gap-2">
                        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                        <div>
                            <p className="font-medium">Institution outbound is halted.</p>
                            <p className="text-xs opacity-80">
                                New outbound campaign sends are blocked until this halt is released.
                            </p>
                        </div>
                    </div>
                </div>
            )}

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
                                                        asChild
                                                        title="Edit in builder"
                                                    >
                                                        <Link to={`/institution-admin/campaigns/${wf.id}/builder`}>
                                                            <Workflow className="h-3.5 w-3.5" />
                                                        </Link>
                                                    </Button>
                                                )}
                                                {wf.status !== "archived" && (
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-8 w-8"
                                                        disabled={busy}
                                                        onClick={() => setArchiveTarget(wf)}
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

            <Dialog open={archiveTarget !== null} onOpenChange={(open) => !open && setArchiveTarget(null)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Archive this campaign?</DialogTitle>
                        <DialogDescription>
                            It will stop accepting new enrollments. Existing runs are not cancelled by archive.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setArchiveTarget(null)} disabled={acting !== null}>
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={() => archiveTarget && handleArchive(archiveTarget)}
                            disabled={acting !== null}
                        >
                            {acting === archiveTarget?.id && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Archive
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={haltDialog !== null} onOpenChange={(open) => !open && setHaltDialog(null)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>
                            {haltDialog === "release" ? "Release outbound halt?" : "Activate outbound halt?"}
                        </DialogTitle>
                        <DialogDescription>
                            {haltDialog === "release"
                                ? "Campaign sends can resume after the halt is released."
                                : "This stops in-flight campaign runs and blocks new outbound sends for this institution."}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setHaltDialog(null)} disabled={acting !== null}>
                            Cancel
                        </Button>
                        <Button
                            variant={haltDialog === "release" ? "default" : "destructive"}
                            onClick={haltDialog === "release" ? handleReleaseHalt : handleActivateHalt}
                            disabled={acting !== null}
                        >
                            {acting === "outbound-halt" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            {haltDialog === "release" ? "Release halt" : "Activate halt"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
