import { useCallback, useEffect, useState, type ReactNode } from "react"
import { Link, useParams } from "react-router-dom"
import {
    ActivitySquare,
    Archive,
    ArrowLeft,
    Ban,
    BarChart3,
    CalendarDays,
    CheckCircle2,
    Clock3,
    DollarSign,
    Filter,
    Hash,
    Loader2,
    Mail,
    MessageSquare,
    Pause,
    Phone,
    Play,
    RefreshCcw,
    Search,
    ShieldAlert,
    TrendingUp,
    UserPlus,
    Users,
    XCircle,
} from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
    archiveCampaign,
    cancelCampaignRun,
    enrollCampaignAudience,
    enrollContactInCampaign,
    emergencyHaltCampaign,
    getCampaignAudience,
    getCampaignAnalytics,
    getCampaign,
    getCampaignOperations,
    getCampaignOverview,
    getRunTimeline,
    getUsageByCampaign,
    getUsageSummary,
    listCampaignRuns,
    pauseCampaign,
    previewCampaignAudience,
    resumeCampaign,
    saveCampaignAudience,
} from "@/lib/automation-api"
import { listContacts, type ContactListItem } from "@/lib/contacts-api"
import { cn } from "@/lib/utils"
import type {
    AutomationWorkflow,
    AutomationWorkflowRun,
    CampaignAnalytics,
    CampaignAudienceExclusions,
    CampaignAudienceFilters,
    CampaignAudiencePreview,
    CampaignOperationItem,
    CampaignOperations,
    CampaignOverview,
    CampaignRunFilters,
    CampaignRunListItem,
    CampaignUsage,
    RunTimeline,
    UsageSummary,
} from "@/types"

const WORKFLOW_STATUS_STYLES: Record<string, string> = {
    active: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-400",
    paused: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-400",
    archived: "border-zinc-200 bg-zinc-100 text-zinc-500 dark:border-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-400",
    draft: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-400",
}

const RUN_STATUS_STYLES: Record<string, string> = {
    pending: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-300",
    running: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-400",
    waiting: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-400",
    completed: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-400",
    cancelled: "border-zinc-200 bg-zinc-100 text-zinc-500 dark:border-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-400",
    failed: "border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-400",
    blocked: "border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-400",
}

const TRIGGER_LABELS: Record<string, string> = {
    appointment_offset: "Appointment reminder",
    recall_scan: "Recall",
    manual: "Manual",
    bulk_import: "Bulk import",
    callback_requested: "Callback",
}

const CHANNEL_LABELS: Record<string, string> = {
    sms: "SMS",
    email: "Email",
    voice: "Voice",
}

const DEFAULT_AUDIENCE_FILTERS: CampaignAudienceFilters = {
    has_no_future_appointment: false,
    recall_due_before: null,
    last_visit_before: null,
    appointment_type_id_in: [],
    provider_id_in: [],
    location_id_in: [],
    preferred_language_in: [],
    contact_channel_available: [],
}

const DEFAULT_AUDIENCE_EXCLUSIONS: CampaignAudienceExclusions = {
    no_consent: true,
    do_not_contact: true,
    suppressed: true,
    contacted_within_days: 1,
    max_contacts_per_rolling_7_days: 3,
    already_enrolled_active: true,
    already_booked: true,
    missing_required_merge_context: true,
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

function elapsed(run: CampaignRunListItem): string {
    const start = run.started_at ? new Date(run.started_at).getTime() : null
    const end = run.completed_at ? new Date(run.completed_at).getTime() : null
    if (!start) return "-"
    const ms = (end ?? Date.now()) - start
    const seconds = Math.max(Math.floor(ms / 1000), 0)
    if (seconds < 60) return `${seconds}s`
    const minutes = Math.floor(seconds / 60)
    if (minutes < 60) return `${minutes}m ${seconds % 60}s`
    return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
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

function percent(value: number | null | undefined): string {
    if (value === null || value === undefined) return "-"
    return `${Math.round(value * 100)}%`
}

function label(value: string | null | undefined): string {
    if (!value) return "-"
    return value.replace(/_/g, " ")
}

function csv(value: string[] | undefined): string {
    return (value ?? []).join(", ")
}

function fromCsv(value: string): string[] {
    return value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
}

function isCancelable(run: Pick<CampaignRunListItem, "status">): boolean {
    return !["completed", "cancelled", "failed", "blocked"].includes(run.status)
}

interface StatProps {
    icon: ReactNode
    label: string
    value: string
    tone?: string
}

function Stat({ icon, label, value, tone }: StatProps) {
    return (
        <div className="rounded-md border border-border bg-card px-4 py-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                {icon}
                {label}
            </div>
            <p className={cn("mt-2 text-xl font-semibold tabular-nums", tone)}>{value}</p>
        </div>
    )
}

function StatusBadge({ status }: { status: string }) {
    return (
        <Badge
            variant="outline"
            className={cn("capitalize", RUN_STATUS_STYLES[status] ?? "border-border")}
        >
            {label(status)}
        </Badge>
    )
}

function WorkflowStatusBadge({ status }: { status: string }) {
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize",
                WORKFLOW_STATUS_STYLES[status] ?? WORKFLOW_STATUS_STYLES.draft,
            )}
        >
            {status}
        </span>
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
        return () => {
            cancelled = true
            clearTimeout(t)
        }
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
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
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

function OverviewTab({
    overview,
    usageSummary,
    campaignUsage,
    loading,
}: {
    overview: CampaignOverview | null
    usageSummary: UsageSummary | null
    campaignUsage: CampaignUsage | null
    loading: boolean
}) {
    const runCounts = overview?.run_counts ?? {}
    const responseCounts = overview?.response_counts ?? {}
    const responseTotal = Object.values(responseCounts).reduce((sum, count) => sum + count, 0)
    const readiness = overview?.readiness
    return (
        <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-5">
                {loading ? (
                    Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)
                ) : (
                    <>
                        <Stat icon={<ActivitySquare className="h-3.5 w-3.5" />} label="Active runs" value={number((runCounts.running ?? 0) + (runCounts.waiting ?? 0) + (runCounts.pending ?? 0))} />
                        <Stat icon={<CheckCircle2 className="h-3.5 w-3.5" />} label="Completed" value={number(runCounts.completed)} tone="text-emerald-600" />
                        <Stat icon={<XCircle className="h-3.5 w-3.5" />} label="Failed or blocked" value={number((runCounts.failed ?? 0) + (runCounts.blocked ?? 0))} tone="text-red-600" />
                        <Stat icon={<MessageSquare className="h-3.5 w-3.5" />} label="Responses" value={number(responseTotal)} />
                        <Stat icon={<Clock3 className="h-3.5 w-3.5" />} label="Readiness" value={label(readiness?.overall_status)} />
                    </>
                )}
            </div>
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base font-semibold">Campaign state</CardTitle>
                    </CardHeader>
                    <CardContent className="grid gap-3 text-sm md:grid-cols-2">
                        <InfoRow label="Latest version" value={overview?.latest_version ? `v${overview.latest_version.version_number}` : "-"} />
                        <InfoRow label="Trigger" value={overview?.trigger_type ? (TRIGGER_LABELS[overview.trigger_type] ?? overview.trigger_type) : "-"} />
                        <InfoRow label="Channels" value={overview?.channels.length ? overview.channels.map((c) => CHANNEL_LABELS[c] ?? c).join(", ") : "-"} />
                        <InfoRow label="Content class" value={label(overview?.latest_version?.content_classification)} />
                        <InfoRow label="Checklist blockers" value={number(readiness?.blockers_count)} />
                        <InfoRow label="Checklist warnings" value={number((readiness?.warnings_count ?? 0) + (readiness?.unknown_count ?? 0))} />
                        <InfoRow label="Open handoffs" value={number(overview?.open_handoff_count)} />
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base font-semibold">Recent outcomes</CardTitle>
                    </CardHeader>
                    <CardContent>
                        {overview?.recent_outcomes.length ? (
                            <ul className="space-y-2">
                                {overview.recent_outcomes.map((row) => (
                                    <li key={row.run_id} className="flex items-center justify-between gap-3 text-sm">
                                        <span className="truncate font-mono text-xs text-muted-foreground">{row.run_id.slice(0, 8)}</span>
                                        <span className="capitalize">{label(row.outcome)}</span>
                                        <span className="text-xs text-muted-foreground">{fmt(row.completed_at ?? row.created_at)}</span>
                                    </li>
                                ))}
                            </ul>
                        ) : (
                            <p className="text-sm text-muted-foreground">No outcomes recorded yet.</p>
                        )}
                    </CardContent>
                </Card>
            </div>
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
                            <Stat icon={<DollarSign className="h-3.5 w-3.5" />} label="Campaign cost" value={money(campaignUsage?.total_cost, usageSummary?.currency)} />
                            <Stat icon={<Hash className="h-3.5 w-3.5" />} label="Events" value={number(campaignUsage?.event_count)} />
                            <Stat icon={<MessageSquare className="h-3.5 w-3.5" />} label="SMS segments" value={number(campaignUsage?.total_segments)} />
                            <Stat icon={<Phone className="h-3.5 w-3.5" />} label="Voice minutes" value={number(campaignUsage?.total_minutes)} />
                            <Stat icon={<Mail className="h-3.5 w-3.5" />} label="Emails" value={number(campaignUsage?.total_emails)} />
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    )
}

function InfoRow({ label, value }: { label: string; value: string }) {
    return (
        <div>
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="mt-1 font-medium capitalize">{value}</p>
        </div>
    )
}

function RunsTab({
    runs,
    loading,
    filters,
    onFiltersChange,
    onSelectRun,
    onCancelRun,
    acting,
    nextCursor,
    onLoadMore,
}: {
    runs: CampaignRunListItem[]
    loading: boolean
    filters: CampaignRunFilters
    onFiltersChange: (filters: CampaignRunFilters) => void
    onSelectRun: (run: CampaignRunListItem) => void
    onCancelRun: (run: CampaignRunListItem) => void
    acting: string | null
    nextCursor: string | null
    onLoadMore: () => void
}) {
    return (
        <Card>
            <CardHeader className="space-y-3 pb-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <CardTitle className="flex items-center gap-2 text-base font-semibold">
                        <ActivitySquare className="h-4 w-4" />
                        Runs
                        {!loading && <span className="text-xs font-normal text-muted-foreground">({runs.length})</span>}
                    </CardTitle>
                </div>
                <RunFilters filters={filters} onChange={onFiltersChange} />
            </CardHeader>
            <CardContent className="p-0">
                {loading ? (
                    <div className="space-y-2 p-4">
                        {Array.from({ length: 5 }).map((_, i) => (
                            <Skeleton key={i} className="h-10 w-full" />
                        ))}
                    </div>
                ) : runs.length === 0 ? (
                    <EmptyState />
                ) : (
                    <>
                        <div className="hidden grid-cols-[1fr_120px_120px_120px_150px_150px_84px] gap-x-4 border-b border-border px-4 py-2 md:grid">
                            <HeaderCell>Patient or run</HeaderCell>
                            <HeaderCell>Status</HeaderCell>
                            <HeaderCell>Step</HeaderCell>
                            <HeaderCell>Outcome</HeaderCell>
                            <HeaderCell>Next action</HeaderCell>
                            <HeaderCell>Elapsed</HeaderCell>
                            <span />
                        </div>
                        <ul className="divide-y divide-border">
                            {runs.map((run) => (
                                <li
                                    key={run.id}
                                    className="grid gap-3 px-4 py-3 md:grid-cols-[1fr_120px_120px_120px_150px_150px_84px] md:items-center md:gap-x-4"
                                >
                                    <button
                                        type="button"
                                        className="min-w-0 text-left"
                                        onClick={() => onSelectRun(run)}
                                    >
                                        <p className="truncate text-sm font-medium">
                                            {run.contact_name ?? "Patient unavailable"}
                                        </p>
                                        <p className="font-mono text-xs text-muted-foreground">{run.id.slice(0, 8)}</p>
                                    </button>
                                    <StatusBadge status={run.status} />
                                    <span className="text-xs capitalize text-muted-foreground">{label(run.current_step_type ?? run.current_step_id)}</span>
                                    <span className="text-xs capitalize text-muted-foreground">{label(run.outcome)}</span>
                                    <span className="text-xs text-muted-foreground">{fmt(run.next_due_at)}</span>
                                    <span className="text-xs text-muted-foreground">{elapsed(run)}</span>
                                    <div className="flex justify-end gap-1">
                                        <Button variant="ghost" size="sm" onClick={() => onSelectRun(run)}>
                                            Timeline
                                        </Button>
                                        {isCancelable(run) && (
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8 text-red-600 hover:text-red-700"
                                                onClick={() => onCancelRun(run)}
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
                        {nextCursor && (
                            <div className="border-t border-border p-3">
                                <Button variant="outline" size="sm" onClick={onLoadMore}>
                                    Load more
                                </Button>
                            </div>
                        )}
                    </>
                )}
            </CardContent>
        </Card>
    )
}

function HeaderCell({ children }: { children: ReactNode }) {
    return <span className="text-xs font-medium text-muted-foreground">{children}</span>
}

function EmptyState() {
    return (
        <div className="flex flex-col items-center gap-2 px-4 py-12 text-center text-muted-foreground">
            <div className="grid size-12 place-items-center rounded-full bg-muted">
                <ActivitySquare className="h-6 w-6 opacity-40" />
            </div>
            <p className="text-sm font-medium text-foreground/70">No runs match this view</p>
            <p className="text-xs">Patients enrolled in this campaign will appear here.</p>
        </div>
    )
}

function RunFilters({
    filters,
    onChange,
}: {
    filters: CampaignRunFilters
    onChange: (filters: CampaignRunFilters) => void
}) {
    const set = (patch: CampaignRunFilters) => onChange({ ...filters, cursor: undefined, ...patch })
    return (
        <div className="grid gap-2 md:grid-cols-[130px_130px_130px_1fr_1fr_1fr_1fr]">
            <Select value={filters.status ?? "all"} onValueChange={(value) => set({ status: value === "all" ? undefined : value })}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                    <SelectItem value="all">All statuses</SelectItem>
                    {["pending", "running", "waiting", "completed", "cancelled", "failed", "blocked"].map((status) => (
                        <SelectItem key={status} value={status}>{label(status)}</SelectItem>
                    ))}
                </SelectContent>
            </Select>
            <Select value={filters.channel ?? "all"} onValueChange={(value) => set({ channel: value === "all" ? undefined : value as "sms" | "email" | "voice" })}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                    <SelectItem value="all">All channels</SelectItem>
                    <SelectItem value="sms">SMS</SelectItem>
                    <SelectItem value="email">Email</SelectItem>
                    <SelectItem value="voice">Voice</SelectItem>
                </SelectContent>
            </Select>
            <Select value={filters.next_due_to ? "due" : "all"} onValueChange={(value) => set(value === "due" ? { next_due_to: new Date().toISOString() } : { next_due_to: undefined })}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                    <SelectItem value="all">Any due time</SelectItem>
                    <SelectItem value="due">Due now</SelectItem>
                </SelectContent>
            </Select>
            <Input
                className="h-8 text-xs"
                placeholder="Outcome"
                value={filters.outcome ?? ""}
                onChange={(event) => set({ outcome: event.target.value || undefined })}
            />
            <Input
                className="h-8 text-xs"
                placeholder="Current step"
                value={filters.current_node ?? ""}
                onChange={(event) => set({ current_node: event.target.value || undefined })}
            />
            <Input
                className="h-8 text-xs"
                placeholder="Patient"
                value={filters.contact_search ?? ""}
                onChange={(event) => set({ contact_search: event.target.value || undefined })}
            />
            <Input
                className="h-8 text-xs"
                placeholder="Failure reason"
                value={filters.failure_reason ?? ""}
                onChange={(event) => set({ failure_reason: event.target.value || undefined })}
            />
        </div>
    )
}

function OperationsTab({
    operations,
    loading,
    onSelectRun,
}: {
    operations: CampaignOperations | null
    loading: boolean
    onSelectRun: (runId: string) => void
}) {
    const sections = [
        ["Patient handoffs", operations?.open_handoffs ?? []],
        ["Stuck waiting", operations?.stuck_waiting_runs ?? []],
        ["Failed sends", operations?.failed_sends ?? []],
        ["Suppressions and skips", operations?.suppressed_skipped_runs ?? []],
    ] as const
    return (
        <div className="grid gap-4 lg:grid-cols-4">
            {sections.map(([title, items]) => (
                <Card key={title}>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base font-semibold">{title}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        {loading ? (
                            <div className="space-y-2">
                                {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
                            </div>
                        ) : items.length === 0 ? (
                            <p className="text-sm text-muted-foreground">No items.</p>
                        ) : (
                            <ul className="space-y-3">
                                {items.map((item) => (
                                    <OperationRow key={item.id} item={item} onSelectRun={onSelectRun} />
                                ))}
                            </ul>
                        )}
                    </CardContent>
                </Card>
            ))}
        </div>
    )
}

function OperationRow({
    item,
    onSelectRun,
}: {
    item: CampaignOperationItem
    onSelectRun: (runId: string) => void
}) {
    return (
        <li className="rounded-md border border-border p-3">
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{item.title}</p>
                    <p className="text-xs text-muted-foreground">{fmt(item.occurred_at)}</p>
                </div>
                <Badge variant="outline" className="capitalize">{label(item.severity)}</Badge>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">{item.reason ?? "Needs review."}</p>
            <div className="mt-3 flex items-center justify-between gap-2">
                <span className="font-mono text-xs text-muted-foreground">{item.run_id.slice(0, 8)}</span>
                <Button variant="outline" size="sm" onClick={() => onSelectRun(item.run_id)}>
                    Timeline
                </Button>
            </div>
        </li>
    )
}

function AudienceTab({ campaign }: { campaign: AutomationWorkflow | null }) {
    const [filters, setFilters] = useState<CampaignAudienceFilters>(DEFAULT_AUDIENCE_FILTERS)
    const [exclusions, setExclusions] = useState<CampaignAudienceExclusions>(DEFAULT_AUDIENCE_EXCLUSIONS)
    const [preview, setPreview] = useState<CampaignAudiencePreview | null>(null)
    const [loading, setLoading] = useState(false)
    const [saving, setSaving] = useState(false)
    const [enrolling, setEnrolling] = useState(false)

    useEffect(() => {
        let cancelled = false
        async function load() {
            if (!campaign) return
            setLoading(true)
            try {
                const definition = await getCampaignAudience(campaign.id)
                if (cancelled) return
                setFilters({ ...DEFAULT_AUDIENCE_FILTERS, ...definition.segment })
                setExclusions({ ...DEFAULT_AUDIENCE_EXCLUSIONS, ...definition.exclusions })
            } catch {
                if (!cancelled) toast.error("Failed to load audience")
            } finally {
                if (!cancelled) setLoading(false)
            }
        }
        load()
        return () => {
            cancelled = true
        }
    }, [campaign])

    if (!campaign) {
        return <Card><CardContent className="p-6 text-sm text-muted-foreground">Campaign not found.</CardContent></Card>
    }
    const activeCampaign = campaign

    const patchFilters = (patch: CampaignAudienceFilters) => {
        setFilters((current) => ({ ...current, ...patch }))
        setPreview(null)
    }
    const patchExclusions = (patch: CampaignAudienceExclusions) => {
        setExclusions((current) => ({ ...current, ...patch }))
        setPreview(null)
    }
    const channelSet = new Set(filters.contact_channel_available ?? [])
    const reasons = Object.entries(preview?.counts_by_reason ?? {}).sort((a, b) => b[1] - a[1])

    async function handleSave() {
        setSaving(true)
        try {
            await saveCampaignAudience(activeCampaign.id, { filters, exclusions })
            toast.success("Audience saved")
        } catch {
            toast.error("Failed to save audience")
        } finally {
            setSaving(false)
        }
    }

    async function handlePreview() {
        setLoading(true)
        try {
            setPreview(await previewCampaignAudience(activeCampaign.id, { filters, exclusions, sample_limit: 40 }))
        } catch {
            toast.error("Failed to preview audience")
        } finally {
            setLoading(false)
        }
    }

    async function handleEnroll() {
        if (!preview) return
        setEnrolling(true)
        try {
            const result = await enrollCampaignAudience(activeCampaign.id, {
                preview_id: preview.preview_id,
                max_enrollments: 500,
            })
            toast.success(`${number(result.enqueued)} patient${result.enqueued === 1 ? "" : "s"} queued`)
            setPreview(await previewCampaignAudience(activeCampaign.id, { filters, exclusions, sample_limit: 40 }))
        } catch {
            toast.error("Audience enrollment is blocked")
        } finally {
            setEnrolling(false)
        }
    }

    return (
        <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-4">
                <Stat icon={<Users className="h-3.5 w-3.5" />} label="Included" value={number(preview?.included_count)} tone="text-emerald-600" />
                <Stat icon={<Ban className="h-3.5 w-3.5" />} label="Excluded" value={number(preview?.excluded_count)} tone="text-red-600" />
                <Stat icon={<Hash className="h-3.5 w-3.5" />} label="Candidates" value={number(preview?.total_candidates)} />
                <Stat icon={<CalendarDays className="h-3.5 w-3.5" />} label="Preview expires" value={preview ? fmt(preview.expires_at) : "-"} />
            </div>

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base font-semibold">
                        <Filter className="h-4 w-4" />
                        Audience
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="grid gap-3 md:grid-cols-3">
                        <label className="space-y-1.5 text-sm">
                            <span className="text-xs font-medium text-muted-foreground">Recall due before</span>
                            <Input
                                type="date"
                                value={filters.recall_due_before ?? ""}
                                onChange={(event) => patchFilters({ recall_due_before: event.target.value || null })}
                            />
                        </label>
                        <label className="space-y-1.5 text-sm">
                            <span className="text-xs font-medium text-muted-foreground">Last visit before</span>
                            <Input
                                type="date"
                                value={filters.last_visit_before ?? ""}
                                onChange={(event) => patchFilters({ last_visit_before: event.target.value || null })}
                            />
                        </label>
                        <label className="space-y-1.5 text-sm">
                            <span className="text-xs font-medium text-muted-foreground">Contacted within days</span>
                            <Input
                                type="number"
                                min={0}
                                max={365}
                                value={exclusions.contacted_within_days ?? ""}
                                onChange={(event) => patchExclusions({ contacted_within_days: event.target.value === "" ? null : Number(event.target.value) })}
                            />
                        </label>
                        <label className="space-y-1.5 text-sm">
                            <span className="text-xs font-medium text-muted-foreground">Appointment type IDs</span>
                            <Input
                                value={csv(filters.appointment_type_id_in)}
                                onChange={(event) => patchFilters({ appointment_type_id_in: fromCsv(event.target.value) })}
                                placeholder="type-1, type-2"
                            />
                        </label>
                        <label className="space-y-1.5 text-sm">
                            <span className="text-xs font-medium text-muted-foreground">Provider IDs</span>
                            <Input
                                value={csv(filters.provider_id_in)}
                                onChange={(event) => patchFilters({ provider_id_in: fromCsv(event.target.value) })}
                                placeholder="provider-1"
                            />
                        </label>
                        <label className="space-y-1.5 text-sm">
                            <span className="text-xs font-medium text-muted-foreground">Location IDs</span>
                            <Input
                                value={csv(filters.location_id_in)}
                                onChange={(event) => patchFilters({ location_id_in: fromCsv(event.target.value) })}
                                placeholder="location-1"
                            />
                        </label>
                    </div>

                    <div className="grid gap-4 lg:grid-cols-2">
                        <div className="space-y-3">
                            <p className="text-xs font-medium text-muted-foreground">Filters</p>
                            <CheckRow
                                label="No future appointment"
                                checked={Boolean(filters.has_no_future_appointment)}
                                onChecked={(checked) => patchFilters({ has_no_future_appointment: checked })}
                            />
                            <div className="flex flex-wrap gap-3">
                                {(["sms", "email", "voice"] as const).map((channel) => (
                                    <CheckRow
                                        key={channel}
                                        label={`${CHANNEL_LABELS[channel]} available`}
                                        checked={channelSet.has(channel)}
                                        onChecked={(checked) => {
                                            const next = new Set(channelSet)
                                            if (checked) next.add(channel)
                                            else next.delete(channel)
                                            patchFilters({ contact_channel_available: Array.from(next) })
                                        }}
                                    />
                                ))}
                            </div>
                        </div>
                        <div className="space-y-3">
                            <p className="text-xs font-medium text-muted-foreground">Exclusions</p>
                            <div className="grid gap-2 sm:grid-cols-2">
                                <CheckRow label="No consent" checked={Boolean(exclusions.no_consent)} onChecked={(checked) => patchExclusions({ no_consent: checked })} />
                                <CheckRow label="Do not contact" checked={Boolean(exclusions.do_not_contact)} onChecked={(checked) => patchExclusions({ do_not_contact: checked })} />
                                <CheckRow label="Suppressed" checked={Boolean(exclusions.suppressed)} onChecked={(checked) => patchExclusions({ suppressed: checked })} />
                                <CheckRow label="Already enrolled" checked={Boolean(exclusions.already_enrolled_active)} onChecked={(checked) => patchExclusions({ already_enrolled_active: checked })} />
                                <CheckRow label="Already booked" checked={Boolean(exclusions.already_booked)} onChecked={(checked) => patchExclusions({ already_booked: checked })} />
                                <CheckRow label="Missing context" checked={Boolean(exclusions.missing_required_merge_context)} onChecked={(checked) => patchExclusions({ missing_required_merge_context: checked })} />
                            </div>
                        </div>
                    </div>

                    <div className="flex flex-wrap justify-end gap-2">
                        <Button variant="outline" onClick={handleSave} disabled={saving || loading}>
                            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Save
                        </Button>
                        <Button variant="outline" onClick={handlePreview} disabled={loading || saving}>
                            {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Preview
                        </Button>
                        <Button onClick={handleEnroll} disabled={!preview || preview.included_count === 0 || activeCampaign.status !== "active" || enrolling}>
                            {enrolling && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Enroll preview
                        </Button>
                    </div>
                </CardContent>
            </Card>

            {preview && (
                <div className="grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
                    <Card>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-base font-semibold">Exclusions</CardTitle>
                        </CardHeader>
                        <CardContent>
                            {reasons.length ? (
                                <ul className="space-y-2">
                                    {reasons.map(([reason, count]) => (
                                        <li key={reason} className="flex items-center justify-between gap-3 text-sm">
                                            <span className="capitalize text-muted-foreground">{label(reason)}</span>
                                            <span className="font-medium tabular-nums">{number(count)}</span>
                                        </li>
                                    ))}
                                </ul>
                            ) : (
                                <p className="text-sm text-muted-foreground">No exclusions in the latest preview.</p>
                            )}
                            {preview.warnings.length > 0 && (
                                <div className="mt-4 space-y-1 border-t border-border pt-3 text-xs text-muted-foreground">
                                    {preview.warnings.map((warning) => <p key={warning}>{warning}</p>)}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-base font-semibold">Sample patients</CardTitle>
                        </CardHeader>
                        <CardContent className="p-0">
                            {preview.samples.length === 0 ? (
                                <p className="p-6 text-sm text-muted-foreground">No sample rows returned.</p>
                            ) : (
                                <ul className="divide-y divide-border">
                                    {preview.samples.map((sample) => (
                                        <li key={`${sample.status}:${sample.contact_id}`} className="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_140px_190px] sm:items-center">
                                            <div className="min-w-0">
                                                <p className="truncate text-sm font-medium">{sample.display_name ?? "Unnamed patient"}</p>
                                                <p className="text-xs text-muted-foreground">{sample.phone_masked ?? sample.email_masked ?? "No channel on file"}</p>
                                            </div>
                                            <Badge variant="outline" className={cn("w-fit capitalize", sample.status === "included" ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-red-200 bg-red-50 text-red-700")}>
                                                {sample.status}
                                            </Badge>
                                            <span className="text-xs capitalize text-muted-foreground">
                                                {sample.reasons.length ? sample.reasons.map(label).join(", ") : "Ready"}
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    )
}

function CheckRow({
    label: text,
    checked,
    onChecked,
}: {
    label: string
    checked: boolean
    onChecked: (checked: boolean) => void
}) {
    return (
        <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={checked} onCheckedChange={(value) => onChecked(value === true)} />
            <span>{text}</span>
        </label>
    )
}

function AnalyticsTab({
    analytics,
    loading,
}: {
    analytics: CampaignAnalytics | null
    loading: boolean
}) {
    const summary = analytics?.summary ?? {}
    const totalSends =
        (summary.sms_sent ?? 0) +
        (summary.voice_attempted ?? 0) +
        (summary.email_sent ?? 0)
    const totalResponses =
        (summary.sms_replied ?? 0) +
        (summary.voice_answered ?? 0) +
        (summary.email_clicked ?? 0)
    return (
        <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-5">
                {loading ? (
                    Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)
                ) : (
                    <>
                        <Stat icon={<UserPlus className="h-3.5 w-3.5" />} label="Enrollments" value={number(summary.enrollments)} />
                        <Stat icon={<BarChart3 className="h-3.5 w-3.5" />} label="Send attempts" value={number(totalSends)} />
                        <Stat icon={<MessageSquare className="h-3.5 w-3.5" />} label="Responses" value={number(totalResponses)} />
                        <Stat icon={<CheckCircle2 className="h-3.5 w-3.5" />} label="Confirmed" value={number(summary.confirmed)} tone="text-emerald-600" />
                        <Stat icon={<DollarSign className="h-3.5 w-3.5" />} label="Cost" value={money(analytics?.cost.total_cost, analytics?.cost.currency)} />
                    </>
                )}
            </div>
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="flex items-center gap-2 text-base font-semibold">
                            <TrendingUp className="h-4 w-4" />
                            Outcome analytics
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        {loading ? (
                            <div className="grid gap-3 md:grid-cols-2">
                                {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}
                            </div>
                        ) : analytics?.outcomes.length ? (
                            <div className="grid gap-3 md:grid-cols-2">
                                {analytics.outcomes.map((outcome) => (
                                    <div key={outcome.key} className="rounded-md border border-border p-4">
                                        <div className="flex items-start justify-between gap-3">
                                            <div className="min-w-0">
                                                <p className="truncate text-sm font-medium">{outcome.label}</p>
                                                <p className="mt-1 text-xs text-muted-foreground">{outcome.description}</p>
                                            </div>
                                            <Badge variant="outline" className="capitalize">{label(outcome.group)}</Badge>
                                        </div>
                                        <div className="mt-4 flex items-end justify-between gap-3">
                                            <span className="text-2xl font-semibold tabular-nums">{number(outcome.count)}</span>
                                            <span className="text-sm text-muted-foreground">{percent(outcome.rate)}</span>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground">No analytics rollup rows for this range.</p>
                        )}
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base font-semibold">Cost per result</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <InfoRow label="Cost per booking" value={analytics?.cost.cost_per_booking === null || analytics?.cost.cost_per_booking === undefined ? "-" : money(analytics.cost.cost_per_booking, analytics.cost.currency)} />
                        <InfoRow label="Cost per confirmation" value={analytics?.cost.cost_per_confirmation === null || analytics?.cost.cost_per_confirmation === undefined ? "-" : money(analytics.cost.cost_per_confirmation, analytics.cost.currency)} />
                        <InfoRow label="Booked" value={number(summary.booked)} />
                        <InfoRow label="Staff handoffs" value={number(summary.staff_handoff)} />
                        <InfoRow label="Rollup freshness" value={analytics?.rollup_fresh_at ? fmt(analytics.rollup_fresh_at) : "-"} />
                    </CardContent>
                </Card>
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base font-semibold">Channel funnel</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {loading ? (
                            Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)
                        ) : analytics?.channels.length ? (
                            analytics.channels.map((channel) => <ChannelFunnel key={channel.channel} channel={channel} />)
                        ) : (
                            <p className="text-sm text-muted-foreground">No channel activity yet.</p>
                        )}
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base font-semibold">Daily trend</CardTitle>
                    </CardHeader>
                    <CardContent>
                        {loading ? (
                            <div className="space-y-2">
                                {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                            </div>
                        ) : analytics?.trend.length ? (
                            <div className="overflow-x-auto">
                                <table className="w-full min-w-[560px] text-sm">
                                    <thead className="text-xs text-muted-foreground">
                                        <tr className="border-b border-border">
                                            <th className="py-2 text-left font-medium">Date</th>
                                            <th className="py-2 text-right font-medium">Enroll</th>
                                            <th className="py-2 text-right font-medium">Sends</th>
                                            <th className="py-2 text-right font-medium">Responses</th>
                                            <th className="py-2 text-right font-medium">Confirmed</th>
                                            <th className="py-2 text-right font-medium">Booked</th>
                                            <th className="py-2 text-right font-medium">Cost</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {analytics.trend.map((point) => (
                                            <tr key={point.date} className="border-b border-border/60 last:border-0">
                                                <td className="py-2">{point.date}</td>
                                                <td className="py-2 text-right tabular-nums">{number(point.enrollments)}</td>
                                                <td className="py-2 text-right tabular-nums">{number(point.sends)}</td>
                                                <td className="py-2 text-right tabular-nums">{number(point.responses)}</td>
                                                <td className="py-2 text-right tabular-nums">{number(point.confirmed)}</td>
                                                <td className="py-2 text-right tabular-nums">{number(point.booked)}</td>
                                                <td className="py-2 text-right tabular-nums">{money(point.total_cost, analytics.cost.currency)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground">No daily metrics in this range.</p>
                        )}
                    </CardContent>
                </Card>
            </div>
        </div>
    )
}

function ChannelFunnel({
    channel,
}: {
    channel: CampaignAnalytics["channels"][number]
}) {
    const max = Math.max(channel.attempted, channel.delivered, channel.responded, channel.failed, 1)
    const rows = [
        ["Attempted", channel.attempted],
        ["Delivered", channel.delivered],
        ["Responded", channel.responded],
        ["Failed", channel.failed],
    ] as const
    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium">{CHANNEL_LABELS[channel.channel] ?? label(channel.channel)}</p>
                <span className="text-xs text-muted-foreground">{number(channel.attempted)} attempts</span>
            </div>
            <div className="space-y-1.5">
                {rows.map(([name, value]) => (
                    <div key={name} className="grid grid-cols-[82px_minmax(0,1fr)_56px] items-center gap-2 text-xs">
                        <span className="text-muted-foreground">{name}</span>
                        <span className="h-2 overflow-hidden rounded-sm bg-muted">
                            <span
                                className={cn(
                                    "block h-full rounded-sm",
                                    name === "Failed" ? "bg-red-500" : "bg-emerald-500",
                                )}
                                style={{ width: `${Math.max((value / max) * 100, value > 0 ? 4 : 0)}%` }}
                            />
                        </span>
                        <span className="text-right tabular-nums">{number(value)}</span>
                    </div>
                ))}
            </div>
        </div>
    )
}

function TimelineDrawer({
    timeline,
    loading,
    onClose,
}: {
    timeline: RunTimeline | null
    loading: boolean
    onClose: () => void
}) {
    return (
        <Sheet open={Boolean(timeline) || loading} onOpenChange={(open) => !open && onClose()}>
            <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
                <SheetHeader>
                    <SheetTitle>Run timeline</SheetTitle>
                    <SheetDescription>
                        {timeline?.contact.display_name ?? "Patient context masked"} ({timeline?.run.id.slice(0, 8) ?? "loading"})
                    </SheetDescription>
                </SheetHeader>
                {loading ? (
                    <div className="mt-6 space-y-3">
                        {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
                    </div>
                ) : timeline ? (
                    <div className="mt-6 space-y-4">
                        <div className="grid gap-3 rounded-md border border-border p-3 text-sm sm:grid-cols-3">
                            <InfoRow label="Status" value={label(timeline.run.status)} />
                            <InfoRow label="Current step" value={label(timeline.run.current_step_type ?? timeline.run.current_step_id)} />
                            <InfoRow label="Outcome" value={label(timeline.run.outcome)} />
                        </div>
                        <ol className="space-y-3">
                            {timeline.items.map((item) => (
                                <li key={`${item.kind}:${item.id}`} className="rounded-md border border-border p-3">
                                    <div className="flex items-start justify-between gap-3">
                                        <div>
                                            <p className="text-sm font-medium">{item.title}</p>
                                            <p className="text-xs text-muted-foreground">{fmt(item.occurred_at)}</p>
                                        </div>
                                        {item.status && <Badge variant="outline" className="capitalize">{label(item.status)}</Badge>}
                                    </div>
                                    {item.summary && <p className="mt-2 text-sm text-muted-foreground">{item.summary}</p>}
                                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                                        {item.channel && <span>Channel: {CHANNEL_LABELS[item.channel] ?? item.channel}</span>}
                                        {item.step_id && <span>Step: {item.step_id}</span>}
                                    </div>
                                </li>
                            ))}
                        </ol>
                    </div>
                ) : null}
            </SheetContent>
        </Sheet>
    )
}

export default function CampaignDetail() {
    const { id } = useParams<{ id: string }>()
    const [campaign, setCampaign] = useState<AutomationWorkflow | null>(null)
    const [overview, setOverview] = useState<CampaignOverview | null>(null)
    const [analytics, setAnalytics] = useState<CampaignAnalytics | null>(null)
    const [runs, setRuns] = useState<CampaignRunListItem[]>([])
    const [nextCursor, setNextCursor] = useState<string | null>(null)
    const [operations, setOperations] = useState<CampaignOperations | null>(null)
    const [usageSummary, setUsageSummary] = useState<UsageSummary | null>(null)
    const [campaignUsage, setCampaignUsage] = useState<CampaignUsage | null>(null)
    const [filters, setFilters] = useState<CampaignRunFilters>({ limit: 50 })
    const [loading, setLoading] = useState(true)
    const [runsLoading, setRunsLoading] = useState(true)
    const [operationsLoading, setOperationsLoading] = useState(true)
    const [timelineLoading, setTimelineLoading] = useState(false)
    const [timeline, setTimeline] = useState<RunTimeline | null>(null)
    const [acting, setActing] = useState<string | null>(null)
    const [archiveOpen, setArchiveOpen] = useState(false)
    const [haltOpen, setHaltOpen] = useState(false)
    const [enrollOpen, setEnrollOpen] = useState(false)
    const [cancelTarget, setCancelTarget] = useState<CampaignRunListItem | null>(null)

    const activeRuns =
        (overview?.run_counts.pending ?? 0) +
        (overview?.run_counts.running ?? 0) +
        (overview?.run_counts.waiting ?? 0)

    const refreshAll = useCallback(async () => {
        if (!id) return
        setLoading(true)
        setRunsLoading(true)
        setOperationsLoading(true)
        try {
            const [wf, ov, analyticsData, runPage, ops, summary, byCampaign] = await Promise.all([
                getCampaign(id),
                getCampaignOverview(id),
                getCampaignAnalytics(id),
                listCampaignRuns(id, { ...filters, cursor: undefined }),
                getCampaignOperations(id),
                getUsageSummary(),
                getUsageByCampaign(undefined, 200),
            ])
            setCampaign(wf)
            setOverview(ov)
            setAnalytics(analyticsData)
            setRuns(runPage.items)
            setNextCursor(runPage.next_cursor)
            setOperations(ops)
            setUsageSummary(summary)
            setCampaignUsage(byCampaign.campaigns.find((row) => row.workflow_id === id) ?? null)
        } catch {
            toast.error("Failed to load campaign")
        } finally {
            setLoading(false)
            setRunsLoading(false)
            setOperationsLoading(false)
        }
    }, [id, filters])

    const refreshRuns = useCallback(async (next?: string | null) => {
        if (!id) return
        setRunsLoading(true)
        try {
            const runPage = await listCampaignRuns(id, { ...filters, cursor: next ?? undefined })
            setRuns((prev) => next ? [...prev, ...runPage.items] : runPage.items)
            setNextCursor(runPage.next_cursor)
        } catch {
            toast.error("Failed to load campaign runs")
        } finally {
            setRunsLoading(false)
        }
    }, [id, filters])

    useEffect(() => {
        refreshAll()
    }, [refreshAll])

    async function handlePause() {
        if (!campaign) return
        setActing("workflow")
        try {
            setCampaign(await pauseCampaign(campaign.id))
            toast.success("Campaign paused")
            await refreshAll()
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
            await refreshAll()
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
            await refreshAll()
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
            await refreshAll()
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
            await cancelCampaignRun(campaign.id, cancelTarget.id)
            toast.success("Run cancelled")
            setCancelTarget(null)
            await refreshAll()
        } catch {
            toast.error("Failed to cancel run")
        } finally {
            setActing(null)
        }
    }

    async function openTimeline(run: CampaignRunListItem) {
        if (!campaign) return
        setTimelineLoading(true)
        setTimeline(null)
        try {
            setTimeline(await getRunTimeline(campaign.id, run.id))
        } catch {
            toast.error("Failed to load run timeline")
        } finally {
            setTimelineLoading(false)
        }
    }

    async function openTimelineById(runId: string) {
        if (!campaign) return
        setTimelineLoading(true)
        setTimeline(null)
        try {
            setTimeline(await getRunTimeline(campaign.id, runId))
        } catch {
            toast.error("Failed to load run timeline")
        } finally {
            setTimelineLoading(false)
        }
    }

    function handleManualEnrolled() {
        refreshAll()
    }

    return (
        <div className="flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="flex items-center gap-3">
                <Button variant="ghost" size="icon" asChild className="h-8 w-8">
                    <Link to="/institution-admin/campaigns">
                        <ArrowLeft className="h-4 w-4" />
                    </Link>
                </Button>
                <span className="text-sm text-muted-foreground">Campaigns</span>
            </div>

            {loading && !campaign ? (
                <div className="space-y-3">
                    <Skeleton className="h-9 w-64" />
                    <Skeleton className="h-5 w-40" />
                </div>
            ) : campaign ? (
                <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                        <h2 className="text-3xl font-bold tracking-tight">{campaign.name}</h2>
                        <div className="flex flex-wrap items-center gap-3">
                            <WorkflowStatusBadge status={campaign.status} />
                            <span className="text-xs text-muted-foreground">
                                {campaign.trigger_type
                                    ? (TRIGGER_LABELS[campaign.trigger_type] ?? campaign.trigger_type)
                                    : "No trigger"}
                            </span>
                            <span className="text-xs text-muted-foreground">
                                {number(activeRuns)} active run{activeRuns === 1 ? "" : "s"}
                            </span>
                        </div>
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                        <Button variant="outline" size="sm" onClick={refreshAll} disabled={loading || acting !== null} className="gap-1.5">
                            <RefreshCcw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                            Refresh
                        </Button>
                        {campaign.status === "active" && (
                            <Button variant="outline" size="sm" disabled={acting !== null} onClick={handlePause} className="gap-1.5">
                                {acting === "workflow" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pause className="h-3.5 w-3.5" />}
                                Pause
                            </Button>
                        )}
                        {campaign.status === "paused" && (
                            <Button variant="outline" size="sm" disabled={acting !== null} onClick={handleResume} className="gap-1.5">
                                {acting === "workflow" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                                Resume
                            </Button>
                        )}
                        {campaign.status !== "archived" && (
                            <>
                                <Button variant="outline" size="sm" disabled={acting !== null || campaign.status !== "active"} onClick={() => setEnrollOpen(true)} className="gap-1.5">
                                    <UserPlus className="h-3.5 w-3.5" />
                                    Enroll
                                </Button>
                                <Button variant="destructive" size="sm" disabled={acting !== null} onClick={() => setHaltOpen(true)} className="gap-1.5">
                                    <ShieldAlert className="h-3.5 w-3.5" />
                                    Halt
                                </Button>
                                <Button variant="outline" size="sm" disabled={acting !== null} onClick={() => setArchiveOpen(true)} className="gap-1.5">
                                    <Archive className="h-3.5 w-3.5" />
                                    Archive
                                </Button>
                            </>
                        )}
                    </div>
                </div>
            ) : (
                <p className="text-sm text-muted-foreground">Campaign not found.</p>
            )}

            <Tabs defaultValue="overview" className="space-y-4">
                <TabsList>
                    <TabsTrigger value="overview">Overview</TabsTrigger>
                    <TabsTrigger value="audience">Audience</TabsTrigger>
                    <TabsTrigger value="runs">Runs</TabsTrigger>
                    <TabsTrigger value="operations">Operations</TabsTrigger>
                    <TabsTrigger value="analytics">Analytics</TabsTrigger>
                </TabsList>
                <TabsContent value="overview">
                    <OverviewTab
                        overview={overview}
                        usageSummary={usageSummary}
                        campaignUsage={campaignUsage}
                        loading={loading}
                    />
                </TabsContent>
                <TabsContent value="audience">
                    <AudienceTab campaign={campaign} />
                </TabsContent>
                <TabsContent value="runs">
                    <RunsTab
                        runs={runs}
                        loading={runsLoading}
                        filters={filters}
                        onFiltersChange={setFilters}
                        onSelectRun={openTimeline}
                        onCancelRun={setCancelTarget}
                        acting={acting}
                        nextCursor={nextCursor}
                        onLoadMore={() => refreshRuns(nextCursor)}
                    />
                </TabsContent>
                <TabsContent value="operations">
                    <OperationsTab
                        operations={operations}
                        loading={operationsLoading}
                        onSelectRun={openTimelineById}
                    />
                </TabsContent>
                <TabsContent value="analytics">
                    <AnalyticsTab analytics={analytics} loading={loading} />
                </TabsContent>
            </Tabs>

            <Dialog open={archiveOpen} onOpenChange={(open) => !open && setArchiveOpen(false)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Archive this campaign?</DialogTitle>
                        <DialogDescription>
                            It will stop accepting new enrollments. Existing runs are not cancelled by archive.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setArchiveOpen(false)} disabled={acting !== null}>Cancel</Button>
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
                        <Button variant="outline" onClick={() => setHaltOpen(false)} disabled={acting !== null}>Cancel</Button>
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
                        <Button variant="outline" onClick={() => setCancelTarget(null)} disabled={acting !== null}>Keep run</Button>
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
            <TimelineDrawer
                timeline={timeline}
                loading={timelineLoading}
                onClose={() => {
                    setTimeline(null)
                    setTimelineLoading(false)
                }}
            />
        </div>
    )
}
