import { useCallback, useEffect, useState, type ReactNode } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import {
    ActivitySquare,
    ArrowLeft,
    Ban,
    CheckCircle2,
    ChevronDown,
    Hash,
    Loader2,
    MessageSquare,
    MoreHorizontal,
    Pause,
    Pencil,
    Phone,
    Play,
    RefreshCcw,
    Search,
    ShieldAlert,
    Trash2,
    UserPlus,
    XCircle,
} from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
    cancelCampaignRun,
    deleteCampaign,
    enrollContactInCampaign,
    emergencyHaltCampaign,
    getCampaign,
    getCampaignAnalytics,
    getCampaignSplitAnalytics,
    getCampaignOverview,
    getUsageByCampaign,
    listCampaignRuns,
    pauseCampaign,
    resumeCampaign,
} from "@/lib/automation-api"
import { listContacts, type ContactListItem } from "@/lib/contacts-api"
import { cn } from "@/lib/utils"
import type {
    AutomationWorkflow,
    AutomationWorkflowRun,
    CampaignAnalytics,
    CampaignSplitAnalytics,
    CampaignOverview,
    CampaignRunFilters,
    CampaignRunListItem,
    CampaignUsage,
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
    patient_status_changed: "Patient status",
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

function number(value: number | undefined): string {
    return new Intl.NumberFormat().format(value ?? 0)
}

function label(value: string | null | undefined): string {
    if (!value) return "-"
    return value.replace(/_/g, " ")
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
            const run = await enrollContactInCampaign(campaign.id, contact.id, campaign.location_id)
            toast.success(`${contact.full_name ?? "Contact"} enrolled`)
            onEnrolled(run)
            onClose()
        } catch (error) {
            const detail = (error as { response?: { data?: { detail?: string } } })
                ?.response?.data?.detail
            toast.error(detail ?? "Failed to enroll contact")
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
                        Enroll contact
                    </DialogTitle>
                    <DialogDescription>
                        Start {campaign.name} for a lead, contact, or patient.
                    </DialogDescription>
                </DialogHeader>
                <div className="relative">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        autoFocus
                        placeholder="Search by name"
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
                        <p className="p-6 text-center text-sm text-muted-foreground">No contacts found.</p>
                    ) : (
                        <ul className="divide-y divide-border">
                            {results.map((contact) => (
                                <li key={contact.id} className="flex items-center justify-between gap-3 px-4 py-3">
                                    <div className="min-w-0">
                                        <p className="truncate text-sm font-medium">
                                            {contact.full_name ?? "Unnamed contact"}
                                        </p>
                                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                                            <Badge variant={contact.lifecycle === "patient" ? "default" : "secondary"} className="capitalize">
                                                {contact.lifecycle}
                                            </Badge>
                                            <span className="text-xs text-muted-foreground">
                                                {contact.phone_masked ?? contact.email_masked ?? "No contact details"}
                                            </span>
                                            {contact.lead_status && (
                                                <span className="text-xs text-muted-foreground">
                                                    · {label(contact.lead_status)}
                                                </span>
                                            )}
                                        </div>
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
    campaignUsage,
    loading,
}: {
    overview: CampaignOverview | null
    campaignUsage: CampaignUsage | null
    loading: boolean
}) {
    const runCounts = overview?.run_counts ?? {}
    const responseCounts = overview?.response_counts ?? {}
    const responseTotal = Object.values(responseCounts).reduce((sum, count) => sum + count, 0)
    return (
        <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-4">
                {loading ? (
                    Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)
                ) : (
                    <>
                        <Stat icon={<ActivitySquare className="h-3.5 w-3.5" />} label="Active runs" value={number((runCounts.running ?? 0) + (runCounts.waiting ?? 0) + (runCounts.pending ?? 0))} />
                        <Stat icon={<CheckCircle2 className="h-3.5 w-3.5" />} label="Completed" value={number(runCounts.completed)} tone="text-emerald-600" />
                        <Stat icon={<XCircle className="h-3.5 w-3.5" />} label="Failed or blocked" value={number((runCounts.failed ?? 0) + (runCounts.blocked ?? 0))} tone="text-red-600" />
                        <Stat icon={<MessageSquare className="h-3.5 w-3.5" />} label="Responses" value={number(responseTotal)} />
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
                        <div className="grid gap-3 md:grid-cols-2">
                            {Array.from({ length: 2 }).map((_, i) => (
                                <Skeleton key={i} className="h-20 w-full" />
                            ))}
                        </div>
                    ) : (
                        <div className="grid gap-3 md:grid-cols-2">
                            <Stat icon={<Hash className="h-3.5 w-3.5" />} label="Events" value={number(campaignUsage?.event_count)} />
                            <Stat icon={<Phone className="h-3.5 w-3.5" />} label="Voice minutes" value={number(campaignUsage?.total_minutes)} />
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

const OUTCOME_GROUP_TONES: Record<string, string> = {
    success: "text-emerald-600",
    failure: "text-red-600",
    neutral: "text-muted-foreground",
}

function rate(value: number | null): string {
    if (value === null) return "-"
    return `${(value * 100).toFixed(1)}%`
}

function money(value: number | null, currency: string): string {
    if (value === null) return "-"
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(value)
}

/**
 * What the campaign achieved, as opposed to what it did.
 *
 * Every figure here comes from the daily rollup, so it answers per campaign and
 * per clinic and lags live runs by a rollup cycle — hence the freshness line.
 * The outcome vocabulary is chosen by the campaign's own category, which is why
 * a recall campaign reads "Recall Booked" where a sales one reads "Qualified"
 * rather than both reporting an anonymous count.
 */
/**
 * Per-variant results for the workflow's Split (A/B) nodes.
 *
 * Rendered only when the campaign actually has a split, so an ordinary campaign
 * is not left explaining an empty experiment panel.
 *
 * The leader badge and the lift column stay hidden until every arm clears
 * `min_arm_enrollments`. The rates show from the first contact — withholding
 * them would read as a broken panel — but a lead on nine contacts is noise, and
 * a UI that dresses it up as a winner will get a test called early.
 */
function SplitResultsSection({ splits }: { splits: CampaignSplitAnalytics }) {
    if (splits.splits.length === 0) return null

    return (
        <div className="space-y-4">
            {splits.splits.map((split) => {
                const totalEnrolled = split.branches.reduce((sum, b) => sum + b.enrollments, 0)
                return (
                    <Card key={split.node_id}>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-base font-semibold">
                                A/B test{split.subject ? `: ${split.subject}` : ""}
                            </CardTitle>
                            <p className="text-xs text-muted-foreground">
                                Compared on {split.primary_outcome_label.toLowerCase()} rate ·{" "}
                                {number(totalEnrolled)} contact(s) in the test
                                {!split.has_enough_volume &&
                                    ` · needs ${number(splits.min_arm_enrollments)} per branch before a winner is called`}
                            </p>
                        </CardHeader>
                        <CardContent>
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="border-b border-border text-left text-xs text-muted-foreground">
                                            <th className="py-2 font-medium">Branch</th>
                                            <th className="py-2 text-right font-medium">Split</th>
                                            <th className="py-2 text-right font-medium">Enrolled</th>
                                            <th className="py-2 text-right font-medium">
                                                {split.primary_outcome_label}
                                            </th>
                                            <th className="py-2 text-right font-medium">Rate</th>
                                            <th className="py-2 text-right font-medium">Lift</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {split.branches.map((branch) => {
                                            const wins =
                                                branch.outcomes.find(
                                                    (row) => row.key === split.primary_outcome_key,
                                                )?.count ?? 0
                                            return (
                                                <tr
                                                    key={branch.label}
                                                    className="border-b border-border/60 last:border-0"
                                                >
                                                    <td className="py-2">
                                                        <span className="font-medium">{branch.label}</span>
                                                        {branch.is_leader && (
                                                            <Badge variant="secondary" className="ml-2 text-xs">
                                                                Leading
                                                            </Badge>
                                                        )}
                                                        {branch.weight === null && (
                                                            <span className="ml-2 text-xs text-muted-foreground">
                                                                no longer in the workflow
                                                            </span>
                                                        )}
                                                    </td>
                                                    <td className="py-2 text-right tabular-nums text-muted-foreground">
                                                        {branch.weight === null ? "—" : `${branch.weight}%`}
                                                    </td>
                                                    <td className="py-2 text-right tabular-nums">
                                                        {number(branch.enrollments)}
                                                    </td>
                                                    <td className="py-2 text-right tabular-nums">
                                                        {number(wins)}
                                                    </td>
                                                    <td className="py-2 text-right tabular-nums">
                                                        {branch.primary_rate === null
                                                            ? "—"
                                                            : `${(branch.primary_rate * 100).toFixed(1)}%`}
                                                    </td>
                                                    <td className="py-2 text-right tabular-nums text-muted-foreground">
                                                        {branch.lift === null
                                                            ? "—"
                                                            : `${branch.lift >= 0 ? "+" : ""}${(branch.lift * 100).toFixed(0)}%`}
                                                    </td>
                                                </tr>
                                            )
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </CardContent>
                    </Card>
                )
            })}
        </div>
    )
}

function OutcomesTab({
    analytics,
    splits,
    loading,
}: {
    analytics: CampaignAnalytics | null
    splits: CampaignSplitAnalytics | null
    loading: boolean
}) {
    if (loading) {
        return (
            <div className="space-y-4">
                <div className="grid gap-3 md:grid-cols-4">
                    {Array.from({ length: 4 }).map((_, i) => (
                        <Skeleton key={i} className="h-20 w-full" />
                    ))}
                </div>
                <Skeleton className="h-64 w-full" />
            </div>
        )
    }

    if (!analytics) {
        return (
            <Card>
                <CardContent className="py-10 text-center text-sm text-muted-foreground">
                    Outcome reporting is unavailable for this campaign right now.
                </CardContent>
            </Card>
        )
    }

    const headline = analytics.outcomes.filter((row) => row.group === "success").slice(0, 4)
    const enrollments = analytics.summary.enrollments ?? 0

    return (
        <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-4">
                <Stat
                    icon={<UserPlus className="h-3.5 w-3.5" />}
                    label="Enrolled"
                    value={number(enrollments)}
                />
                {headline.map((row) => (
                    <Stat
                        key={row.key}
                        icon={<CheckCircle2 className="h-3.5 w-3.5" />}
                        label={row.label}
                        value={number(row.count)}
                        tone="text-emerald-600"
                    />
                ))}
            </div>

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-base font-semibold">Outcomes</CardTitle>
                </CardHeader>
                <CardContent>
                    {analytics.outcomes.length ? (
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                                    <th className="pb-2 font-medium">Outcome</th>
                                    <th className="pb-2 text-right font-medium">Count</th>
                                    <th className="pb-2 text-right font-medium">Of enrolled</th>
                                </tr>
                            </thead>
                            <tbody>
                                {analytics.outcomes.map((row) => (
                                    <tr key={row.key} className="border-b border-border/50 last:border-0">
                                        <td className="py-2">
                                            <p className={cn("font-medium", OUTCOME_GROUP_TONES[row.group])}>
                                                {row.label}
                                            </p>
                                            <p className="text-xs text-muted-foreground">{row.description}</p>
                                        </td>
                                        <td className="py-2 text-right tabular-nums">{number(row.count)}</td>
                                        <td className="py-2 text-right tabular-nums text-muted-foreground">
                                            {rate(row.rate)}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    ) : (
                        <p className="text-sm text-muted-foreground">No outcomes recorded yet.</p>
                    )}
                </CardContent>
            </Card>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base font-semibold">Delivery by channel</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                                    <th className="pb-2 font-medium">Channel</th>
                                    <th className="pb-2 text-right font-medium">Sent</th>
                                    <th className="pb-2 text-right font-medium">Delivered</th>
                                    <th className="pb-2 text-right font-medium">Failed</th>
                                    <th className="pb-2 text-right font-medium">Responded</th>
                                </tr>
                            </thead>
                            <tbody>
                                {analytics.channels.map((row) => (
                                    <tr key={row.channel} className="border-b border-border/50 last:border-0">
                                        <td className="py-2 capitalize">{row.channel}</td>
                                        <td className="py-2 text-right tabular-nums">{number(row.attempted)}</td>
                                        <td className="py-2 text-right tabular-nums">{number(row.delivered)}</td>
                                        <td className="py-2 text-right tabular-nums">{number(row.failed)}</td>
                                        <td className="py-2 text-right tabular-nums">{number(row.responded)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base font-semibold">Cost per outcome</CardTitle>
                    </CardHeader>
                    <CardContent className="grid gap-3 text-sm">
                        <InfoRow
                            label="Total spend"
                            value={money(analytics.cost.total_cost, analytics.cost.currency)}
                        />
                        <InfoRow
                            label="Per booking"
                            value={money(analytics.cost.cost_per_booking, analytics.cost.currency)}
                        />
                        <InfoRow
                            label="Per confirmation"
                            value={money(analytics.cost.cost_per_confirmation, analytics.cost.currency)}
                        />
                        {/* Revenue attribution is deliberately absent: the rule that
                            decides it has not been agreed, and the figure has to be
                            shown next to its rule or it starts arguments instead of
                            settling them. */}
                        <p className="border-t border-border pt-3 text-xs text-muted-foreground">
                            Revenue attributed to this campaign is not reported yet. It needs an
                            agreed attribution rule, which is shown alongside the figure once set.
                        </p>
                    </CardContent>
                </Card>
            </div>

            {splits && <SplitResultsSection splits={splits} />}

            <p className="text-xs text-muted-foreground">
                {analytics.start_date} to {analytics.end_date}
                {analytics.rollup_fresh_at
                    ? ` · rolled up ${fmt(analytics.rollup_fresh_at)}`
                    : " · not yet rolled up"}
            </p>
        </div>
    )
}

function ExecutionsTab({
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
                        Executions
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
                                            Inspect
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
            <p className="text-sm font-medium text-foreground/70">No executions match this view</p>
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
    // Keep the extra filters open if the caller arrived with any of them set.
    const [showMore, setShowMore] = useState(
        Boolean(filters.outcome || filters.current_node || filters.failure_reason || filters.next_due_to),
    )
    return (
        <div className="space-y-2">
            <div className="grid gap-2 md:grid-cols-[150px_1fr_auto]">
                <Select value={filters.status ?? "all"} onValueChange={(value) => set({ status: value === "all" ? undefined : value })}>
                    <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All statuses</SelectItem>
                        {["pending", "running", "waiting", "completed", "cancelled", "failed", "blocked"].map((status) => (
                            <SelectItem key={status} value={status}>{label(status)}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <Input
                    className="h-8 text-xs"
                    placeholder="Patient"
                    value={filters.contact_search ?? ""}
                    onChange={(event) => set({ contact_search: event.target.value || undefined })}
                />
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-8 gap-1.5 text-xs"
                    aria-expanded={showMore}
                    onClick={() => setShowMore((open) => !open)}
                >
                    More filters
                    <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", showMore && "rotate-180")} />
                </Button>
            </div>
            {showMore && (
                <div className="grid gap-2 md:grid-cols-[130px_1fr_1fr_1fr]">
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
                        placeholder="Failure reason"
                        value={filters.failure_reason ?? ""}
                        onChange={(event) => set({ failure_reason: event.target.value || undefined })}
                    />
                </div>
            )}
        </div>
    )
}

export default function CampaignDetail() {
    const { id } = useParams<{ id: string }>()
    const navigate = useNavigate()
    const [campaign, setCampaign] = useState<AutomationWorkflow | null>(null)
    const [overview, setOverview] = useState<CampaignOverview | null>(null)
    const [runs, setRuns] = useState<CampaignRunListItem[]>([])
    const [nextCursor, setNextCursor] = useState<string | null>(null)
    const [campaignUsage, setCampaignUsage] = useState<CampaignUsage | null>(null)
    const [analytics, setAnalytics] = useState<CampaignAnalytics | null>(null)
    const [splitAnalytics, setSplitAnalytics] = useState<CampaignSplitAnalytics | null>(null)
    const [filters, setFilters] = useState<CampaignRunFilters>({ limit: 50 })
    const [loading, setLoading] = useState(true)
    const [runsLoading, setRunsLoading] = useState(true)
    const [acting, setActing] = useState<string | null>(null)
    const [deleteOpen, setDeleteOpen] = useState(false)
    const [haltOpen, setHaltOpen] = useState(false)
    const [enrollOpen, setEnrollOpen] = useState(false)
    const [cancelTarget, setCancelTarget] = useState<CampaignRunListItem | null>(null)

    const activeRuns =
        (overview?.run_counts.pending ?? 0) +
        (overview?.run_counts.running ?? 0) +
        (overview?.run_counts.waiting ?? 0)

    // Deliberately independent of `filters`: changing a run filter must refetch
    // runs only, not the overview / usage panels alongside them.
    const refreshAll = useCallback(async () => {
        if (!id) return
        setLoading(true)
        try {
            const [wf, ov, byCampaign, outcomes, splits] = await Promise.all([
                getCampaign(id),
                getCampaignOverview(id),
                getUsageByCampaign(undefined, 1, { workflowId: id }),
                // Outcome analytics read the daily rollup rather than live runs.
                // A stale or failed rollup should cost the reporting tab its
                // numbers, not take the whole campaign page down with it.
                getCampaignAnalytics(id).catch(() => null),
                // Same rollup, cut by split arm. Absent for the many campaigns
                // that run no experiment, so its failure is equally survivable.
                getCampaignSplitAnalytics(id).catch(() => null),
            ])
            setCampaign(wf)
            setOverview(ov)
            setCampaignUsage(byCampaign.campaigns.find((row) => row.workflow_id === id) ?? null)
            setAnalytics(outcomes)
            setSplitAnalytics(splits)
        } catch {
            toast.error("Failed to load campaign")
        } finally {
            setLoading(false)
        }
    }, [id])

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

    // Runs reload on their own whenever the filter set changes.
    useEffect(() => {
        refreshRuns()
    }, [refreshRuns])

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

    async function handleDelete() {
        if (!campaign) return
        setActing("delete")
        try {
            await deleteCampaign(campaign.id)
            toast.success("Campaign deleted")
            setDeleteOpen(false)
            navigate("/institution-admin/campaigns")
        } catch {
            toast.error("Failed to delete campaign")
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

    function openExecution(run: CampaignRunListItem) {
        if (!campaign) return
        navigate(`/institution-admin/campaigns/${campaign.id}/builder?view=executions&run=${encodeURIComponent(run.id)}`)
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
                        <Button variant="outline" size="sm" asChild className="gap-1.5">
                            <Link to={`/institution-admin/campaigns/${campaign.id}/builder`}>
                                <Pencil className="h-3.5 w-3.5" />
                                Edit workflow
                            </Link>
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
                            <Button variant="outline" size="sm" disabled={acting !== null || campaign.status !== "active"} onClick={() => setEnrollOpen(true)} className="gap-1.5">
                                <UserPlus className="h-3.5 w-3.5" />
                                Enroll
                            </Button>
                        )}
                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <Button variant="ghost" size="icon" className="h-8 w-8" disabled={acting !== null} aria-label="More actions">
                                    <MoreHorizontal className="h-4 w-4" />
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                                <DropdownMenuItem disabled={loading} onSelect={() => refreshAll()}>
                                    <RefreshCcw className={cn("mr-2 h-3.5 w-3.5", loading && "animate-spin")} />
                                    Refresh
                                </DropdownMenuItem>
                                {campaign.status !== "archived" && (
                                    <>
                                        <DropdownMenuSeparator />
                                        <DropdownMenuItem
                                            className="text-destructive focus:text-destructive"
                                            onSelect={() => setHaltOpen(true)}
                                        >
                                            <ShieldAlert className="mr-2 h-3.5 w-3.5" />
                                            Emergency halt
                                        </DropdownMenuItem>
                                    </>
                                )}
                                <DropdownMenuItem
                                    className="text-destructive focus:text-destructive"
                                    onSelect={() => setDeleteOpen(true)}
                                >
                                    <Trash2 className="mr-2 h-3.5 w-3.5" />
                                    Delete campaign
                                </DropdownMenuItem>
                            </DropdownMenuContent>
                        </DropdownMenu>
                    </div>
                </div>
            ) : (
                <p className="text-sm text-muted-foreground">Campaign not found.</p>
            )}

            <Tabs defaultValue="overview" className="space-y-4">
                <TabsList>
                    <TabsTrigger value="overview">Overview</TabsTrigger>
                    <TabsTrigger value="outcomes">Outcomes</TabsTrigger>
                    <TabsTrigger value="executions">Executions</TabsTrigger>
                </TabsList>
                <TabsContent value="overview">
                    <OverviewTab
                        overview={overview}
                        campaignUsage={campaignUsage}
                        loading={loading}
                    />
                </TabsContent>
                <TabsContent value="outcomes">
                    <OutcomesTab analytics={analytics} splits={splitAnalytics} loading={loading} />
                </TabsContent>
                <TabsContent value="executions">
                    <ExecutionsTab
                        runs={runs}
                        loading={runsLoading}
                        filters={filters}
                        onFiltersChange={setFilters}
                        onSelectRun={openExecution}
                        onCancelRun={setCancelTarget}
                        acting={acting}
                        nextCursor={nextCursor}
                        onLoadMore={() => refreshRuns(nextCursor)}
                    />
                </TabsContent>
            </Tabs>

            <Dialog open={deleteOpen} onOpenChange={(open) => !open && setDeleteOpen(false)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Delete this campaign?</DialogTitle>
                        <DialogDescription>
                            This permanently removes the campaign, its versions, runs, timers, and campaign-owned history.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDeleteOpen(false)} disabled={acting !== null}>Cancel</Button>
                        <Button type="button" variant="destructive" onClick={handleDelete} disabled={acting !== null}>
                            {acting === "delete" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Delete campaign
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
        </div>
    )
}
