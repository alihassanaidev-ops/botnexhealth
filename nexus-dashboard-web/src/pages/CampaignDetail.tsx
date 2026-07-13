import { useEffect, useState, type ReactNode } from "react"
import { Link, useParams } from "react-router-dom"
import {
    ArrowLeft,
    RefreshCcw,
    Pause,
    Play,
    Archive,
    Loader2,
    ActivitySquare,
    ShieldAlert,
    Ban,
    DollarSign,
    MessageSquare,
    Phone,
    Mail,
    Hash,
    UserPlus,
    Search,
} from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Input } from "@/components/ui/input"
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
    archiveCampaign,
    cancelCampaignRun,
    enrollContactInCampaign,
    emergencyHaltCampaign,
    getCampaign,
    getUsageByCampaign,
    getUsageSummary,
    listCampaignRuns,
    pauseCampaign,
    resumeCampaign,
} from "@/lib/automation-api"
import {
    listContacts,
    type ContactListItem,
} from "@/lib/contacts-api"
import type {
    AutomationWorkflow,
    AutomationWorkflowRun,
    CampaignUsage,
    UsageSummary,
} from "@/types"

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

const TRIGGER_LABELS: Record<string, string> = {
    appointment_offset: "Appointment reminder",
    recall_scan: "Recall / reactivation",
    manual: "Manual",
}

function fmt(iso: string | null): string {
    if (!iso) return "-"
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
    if (!start) return "-"
    const ms = (end ?? Date.now()) - start
    const s = Math.max(Math.floor(ms / 1000), 0)
    if (s < 60) return `${s}s`
    const m = Math.floor(s / 60)
    if (m < 60) return `${m}m ${s % 60}s`
    return `${Math.floor(m / 60)}h ${m % 60}m`
}

function money(value: number | undefined, currency = "USD"): string {
    return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency,
        maximumFractionDigits: 2,
    }).format(value ?? 0)
}

function number(value: number | undefined): string {
    return new Intl.NumberFormat().format(value ?? 0)
}

function isCancelable(run: AutomationWorkflowRun): boolean {
    return !["completed", "cancelled", "failed"].includes(run.status)
}

interface StatProps {
    icon: ReactNode
    label: string
    value: string
}

function Stat({ icon, label, value }: StatProps) {
    return (
        <div className="rounded-md border border-border bg-card px-4 py-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                {icon}
                {label}
            </div>
            <p className="mt-2 text-xl font-semibold tabular-nums">{value}</p>
        </div>
    )
}

interface ManualEnrollDialogProps {
    campaign: AutomationWorkflow
    onClose: () => void
    onEnrolled: (run: AutomationWorkflowRun) => void
}

function ManualEnrollDialog({ campaign, onClose, onEnrolled }: ManualEnrollDialogProps) {
    const [search, setSearch] = useState("")
    const [results, setResults] = useState<ContactListItem[]>([])
    const [loading, setLoading] = useState(false)
    const [enrolling, setEnrolling] = useState<string | null>(null)

    useEffect(() => {
        let cancelled = false
        const t = setTimeout(async () => {
            setLoading(true)
            try {
                const res = await listContacts({ limit: 10, search: search || undefined })
                if (!cancelled) setResults(res.items)
            } catch {
                if (!cancelled) setResults([])
            } finally {
                if (!cancelled) setLoading(false)
            }
        }, 250)
        return () => { cancelled = true; clearTimeout(t) }
    }, [search])

    async function enroll(contact: ContactListItem) {
        setEnrolling(contact.id)
        try {
            const run = await enrollContactInCampaign(campaign.id, contact.id)
            toast.success(`${contact.full_name ?? "Patient"} enrolled`)
            onEnrolled(run)
            onClose()
        } catch {
            toast.error("Failed to enroll patient")
        } finally {
            setEnrolling(null)
        }
    }

    return (
        <Dialog open onOpenChange={(open) => !open && !enrolling && onClose()}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <UserPlus className="h-5 w-5" />
                        Enroll patient
                    </DialogTitle>
                    <DialogDescription>
                        Start {campaign.name} for one existing patient.
                    </DialogDescription>
                </DialogHeader>
                <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                    <Input
                        autoFocus
                        placeholder="Search patients by name"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        className="pl-8"
                    />
                </div>
                <div className="max-h-80 overflow-y-auto rounded-md border border-border">
                    {loading ? (
                        <div className="space-y-2 p-4">
                            {Array.from({ length: 4 }).map((_, i) => (
                                <Skeleton key={i} className="h-10 w-full" />
                            ))}
                        </div>
                    ) : results.length === 0 ? (
                        <p className="p-6 text-center text-sm text-muted-foreground">No patients found.</p>
                    ) : (
                        <ul className="divide-y divide-border">
                            {results.map((contact) => (
                                <li key={contact.id} className="flex items-center justify-between gap-3 px-4 py-3">
                                    <div className="min-w-0">
                                        <p className="truncate text-sm font-medium">
                                            {contact.full_name ?? "Unnamed patient"}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            {contact.phone_masked ?? "No phone on file"}
                                        </p>
                                    </div>
                                    <Button
                                        size="sm"
                                        onClick={() => enroll(contact)}
                                        disabled={enrolling !== null || campaign.status !== "active"}
                                    >
                                        {enrolling === contact.id && (
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        )}
                                        Enroll
                                    </Button>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
                {campaign.status !== "active" && (
                    <p className="text-xs text-muted-foreground">
                        Only active campaigns can accept manual enrollments.
                    </p>
                )}
            </DialogContent>
        </Dialog>
    )
}

export default function CampaignDetail() {
    const { id } = useParams<{ id: string }>()
    const [campaign, setCampaign] = useState<AutomationWorkflow | null>(null)
    const [runs, setRuns] = useState<AutomationWorkflowRun[]>([])
    const [usageSummary, setUsageSummary] = useState<UsageSummary | null>(null)
    const [campaignUsage, setCampaignUsage] = useState<CampaignUsage | null>(null)
    const [loading, setLoading] = useState(true)
    const [runsLoading, setRunsLoading] = useState(true)
    const [acting, setActing] = useState<string | null>(null)
    const [archiveOpen, setArchiveOpen] = useState(false)
    const [haltOpen, setHaltOpen] = useState(false)
    const [enrollOpen, setEnrollOpen] = useState(false)
    const [cancelTarget, setCancelTarget] = useState<AutomationWorkflowRun | null>(null)

    async function refresh() {
        if (!id) return
        setLoading(true)
        setRunsLoading(true)
        try {
            const [wf, wfRuns, summary, byCampaign] = await Promise.all([
                getCampaign(id),
                listCampaignRuns(id),
                getUsageSummary(),
                // The /by-campaign endpoint has no per-workflow filter; it returns the
                // top-N workflows by spend. We request the backend max (200) and pick this
                // campaign out client-side. Caveat: for an institution with >200 campaigns,
                // a campaign outside the top-200-by-spend will not appear here and its usage
                // cards fall back to the neutral empty state (0). If exact per-campaign usage
                // for such tail campaigns is required, add a workflow_id filter to the route.
                getUsageByCampaign(undefined, 200),
            ])
            setCampaign(wf)
            setRuns(wfRuns)
            setUsageSummary(summary)
            setCampaignUsage(byCampaign.campaigns.find((row) => row.workflow_id === id) ?? null)
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
        setActing("workflow")
        try {
            setCampaign(await pauseCampaign(campaign.id))
            toast.success("Campaign paused")
        } catch {
            toast.error("Failed to pause campaign")
        } finally {
            setActing(null)
        }
    }

    async function handleResume() {
        if (!campaign) return
        setActing("workflow")
        try {
            setCampaign(await resumeCampaign(campaign.id))
            toast.success("Campaign resumed")
        } catch {
            toast.error("Failed to resume campaign")
        } finally {
            setActing(null)
        }
    }

    async function handleArchive() {
        if (!campaign) return
        setActing("archive")
        try {
            setCampaign(await archiveCampaign(campaign.id))
            toast.success("Campaign archived")
            setArchiveOpen(false)
        } catch {
            toast.error("Failed to archive campaign")
        } finally {
            setActing(null)
        }
    }

    async function handleEmergencyHalt() {
        if (!campaign) return
        setActing("halt")
        try {
            const result = await emergencyHaltCampaign(campaign.id, "Activated from campaign detail")
            setCampaign((prev) => prev ? { ...prev, status: result.status as AutomationWorkflow["status"] } : prev)
            toast.success(`Campaign halted. ${result.halted_runs} runs stopped.`)
            setHaltOpen(false)
            await refresh()
        } catch {
            toast.error("Failed to halt campaign")
        } finally {
            setActing(null)
        }
    }

    async function handleCancelRun() {
        if (!campaign || !cancelTarget) return
        setActing(cancelTarget.id)
        try {
            const updated = await cancelCampaignRun(campaign.id, cancelTarget.id)
            setRuns((prev) => prev.map((run) => run.id === updated.id ? updated : run))
            toast.success("Run cancelled")
            setCancelTarget(null)
        } catch {
            toast.error("Failed to cancel run")
        } finally {
            setActing(null)
        }
    }

    function handleManualEnrolled(run: AutomationWorkflowRun) {
        setRuns((prev) => [run, ...prev.filter((existing) => existing.id !== run.id)])
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
                    <div className="flex flex-wrap items-center justify-end gap-2">
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={refresh}
                            disabled={loading || acting !== null}
                            className="gap-1.5"
                        >
                            <RefreshCcw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                            Refresh
                        </Button>
                        {campaign.status === "active" && (
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={acting !== null}
                                onClick={handlePause}
                                className="gap-1.5"
                            >
                                {acting === "workflow" ? (
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
                                disabled={acting !== null}
                                onClick={handleResume}
                                className="gap-1.5"
                            >
                                {acting === "workflow" ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                    <Play className="h-3.5 w-3.5" />
                                )}
                                Resume
                            </Button>
                        )}
                        {campaign.status !== "archived" && (
                            <>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={acting !== null || campaign.status !== "active"}
                                    onClick={() => setEnrollOpen(true)}
                                    className="gap-1.5"
                                >
                                    <UserPlus className="h-3.5 w-3.5" />
                                    Enroll
                                </Button>
                                <Button
                                    variant="destructive"
                                    size="sm"
                                    disabled={acting !== null}
                                    onClick={() => setHaltOpen(true)}
                                    className="gap-1.5"
                                >
                                    <ShieldAlert className="h-3.5 w-3.5" />
                                    Halt
                                </Button>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={acting !== null}
                                    onClick={() => setArchiveOpen(true)}
                                    className="gap-1.5"
                                >
                                    <Archive className="h-3.5 w-3.5" />
                                    Archive
                                </Button>
                            </>
                        )}
                    </div>
                </div>
            ) : (
                <p className="text-muted-foreground text-sm">Campaign not found.</p>
            )}

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-base font-semibold">Usage, last 30 days</CardTitle>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="grid gap-3 md:grid-cols-5">
                            {Array.from({ length: 5 }).map((_, i) => (
                                <Skeleton key={i} className="h-20 w-full" />
                            ))}
                        </div>
                    ) : (
                        <div className="grid gap-3 md:grid-cols-5">
                            <Stat
                                icon={<DollarSign className="h-3.5 w-3.5" />}
                                label="Campaign cost"
                                value={money(campaignUsage?.total_cost, usageSummary?.currency)}
                            />
                            <Stat
                                icon={<Hash className="h-3.5 w-3.5" />}
                                label="Campaign events"
                                value={number(campaignUsage?.event_count)}
                            />
                            <Stat
                                icon={<MessageSquare className="h-3.5 w-3.5" />}
                                label="SMS segments"
                                value={number(campaignUsage?.total_segments)}
                            />
                            <Stat
                                icon={<Phone className="h-3.5 w-3.5" />}
                                label="Voice minutes"
                                value={number(campaignUsage?.total_minutes)}
                            />
                            <Stat
                                icon={<Mail className="h-3.5 w-3.5" />}
                                label="Emails"
                                value={number(campaignUsage?.total_emails)}
                            />
                        </div>
                    )}
                </CardContent>
            </Card>

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base font-semibold">
                        <ActivitySquare className="h-4 w-4" />
                        Runs
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
                            <p className="text-sm font-medium text-foreground/70">No runs yet</p>
                            <p className="text-xs">Patients enrolled in this campaign will appear here.</p>
                        </div>
                    ) : (
                        <>
                            <div className="grid grid-cols-[1fr_100px_100px_140px_140px_80px_70px] gap-x-4 border-b border-border px-4 py-2">
                                <span className="text-xs font-medium text-muted-foreground">Run ID</span>
                                <span className="text-xs font-medium text-muted-foreground">Status</span>
                                <span className="text-xs font-medium text-muted-foreground">Outcome</span>
                                <span className="text-xs font-medium text-muted-foreground">Started</span>
                                <span className="text-xs font-medium text-muted-foreground">Completed</span>
                                <span className="text-xs font-medium text-muted-foreground">Elapsed</span>
                                <span />
                            </div>
                            <ul className="divide-y divide-border">
                                {runs.map((run) => (
                                    <li
                                        key={run.id}
                                        className="grid grid-cols-[1fr_100px_100px_140px_140px_80px_70px] items-center gap-x-4 px-4 py-2.5"
                                    >
                                        <span className="font-mono text-xs text-muted-foreground truncate">
                                            {run.id.slice(0, 8)}...
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
                                            {run.outcome ?? "-"}
                                        </span>
                                        <span className="text-xs text-muted-foreground">{fmt(run.started_at)}</span>
                                        <span className="text-xs text-muted-foreground">{fmt(run.completed_at)}</span>
                                        <span className="text-xs text-muted-foreground">{elapsed(run)}</span>
                                        <div className="flex justify-end">
                                            {isCancelable(run) && (
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-8 w-8 text-red-600 hover:text-red-700"
                                                    onClick={() => setCancelTarget(run)}
                                                    disabled={acting !== null}
                                                    title="Cancel run"
                                                >
                                                    {acting === run.id ? (
                                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                    ) : (
                                                        <Ban className="h-3.5 w-3.5" />
                                                    )}
                                                </Button>
                                            )}
                                        </div>
                                    </li>
                                ))}
                            </ul>
                        </>
                    )}
                </CardContent>
            </Card>

            <Dialog open={archiveOpen} onOpenChange={(open) => !open && setArchiveOpen(false)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Archive this campaign?</DialogTitle>
                        <DialogDescription>
                            It will stop accepting new enrollments. Existing runs are not cancelled by archive.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setArchiveOpen(false)} disabled={acting !== null}>
                            Cancel
                        </Button>
                        <Button variant="destructive" onClick={handleArchive} disabled={acting !== null}>
                            {acting === "archive" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Archive
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={haltOpen} onOpenChange={(open) => !open && setHaltOpen(false)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Emergency halt this campaign?</DialogTitle>
                        <DialogDescription>
                            This terminates in-flight runs for the current campaign version and pauses the campaign.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setHaltOpen(false)} disabled={acting !== null}>
                            Cancel
                        </Button>
                        <Button variant="destructive" onClick={handleEmergencyHalt} disabled={acting !== null}>
                            {acting === "halt" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Halt campaign
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={cancelTarget !== null} onOpenChange={(open) => !open && setCancelTarget(null)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Cancel this run?</DialogTitle>
                        <DialogDescription>
                            This stops the selected campaign run and cancels its pending timers.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setCancelTarget(null)} disabled={acting !== null}>
                            Keep run
                        </Button>
                        <Button variant="destructive" onClick={handleCancelRun} disabled={acting !== null}>
                            {acting === cancelTarget?.id && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Cancel run
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {campaign && enrollOpen && (
                <ManualEnrollDialog
                    campaign={campaign}
                    onClose={() => setEnrollOpen(false)}
                    onEnrolled={handleManualEnrolled}
                />
            )}
        </div>
    )
}
