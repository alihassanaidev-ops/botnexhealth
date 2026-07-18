import { AlertCircle, AlertTriangle, CheckCircle2, CircleHelp, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import type { LaunchChecklist, LaunchChecklistItem, LaunchChecklistStatus } from "@/types/workflow"

const STATUS_STYLES: Record<LaunchChecklistStatus, string> = {
    pass: "text-emerald-600 dark:text-emerald-400",
    warning: "text-amber-600 dark:text-amber-400",
    blocked: "text-red-600 dark:text-red-400",
    unknown: "text-slate-500 dark:text-slate-400",
}

const SECTION_LABELS: Record<string, string> = {
    workflow: "Workflow",
    content: "Content",
    channels: "Channels",
    compliance: "Compliance",
    audience: "Audience",
    data: "Data",
    operations: "Operations",
    estimates: "Estimates",
}

export interface LaunchChecklistPanelProps {
    checklist: LaunchChecklist | null
    loading?: boolean
    compact?: boolean
}

export default function LaunchChecklistPanel({
    checklist,
    loading = false,
    compact = false,
}: LaunchChecklistPanelProps) {
    if (loading && !checklist) {
        return (
            <div className="flex items-center gap-2 rounded-md border border-border px-3 py-2.5 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Checking launch readiness
            </div>
        )
    }

    if (!checklist) {
        return (
            <div className="rounded-md border border-border px-3 py-2.5 text-sm text-muted-foreground">
                Launch checklist unavailable.
            </div>
        )
    }

    const items = compact ? checklist.items.filter((item) => item.status !== "pass") : checklist.items
    const grouped = groupItems(items.length ? items : checklist.items)

    return (
        <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                    <div className="text-sm font-semibold text-foreground">Launch checklist</div>
                    <div className="text-xs text-muted-foreground">
                        {checklist.blockers_count} blockers · {checklist.warnings_count} warnings · {checklist.unknown_count} unknown
                    </div>
                </div>
                <StatusPill status={checklist.overall_status} />
            </div>

            <div className="grid grid-cols-3 gap-2 text-xs">
                <Metric label="Audience" value={checklist.estimated_audience === null ? "Unknown" : checklist.estimated_audience.toLocaleString()} />
                <Metric label="Volume" value={formatVolume(checklist.estimated_send_volume)} />
                <Metric label="Cost" value={formatCost(checklist.estimated_cost_cents)} />
            </div>
            {!compact && <p className="text-xs text-muted-foreground">{checklist.estimate_basis}</p>}

            <div className="space-y-3">
                {Array.from(grouped.entries()).map(([section, sectionItems]) => (
                    <div key={section} className="space-y-1.5">
                        <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                            {SECTION_LABELS[section] ?? section}
                        </div>
                        <ul className="space-y-1.5">
                            {sectionItems.map((item) => (
                                <ChecklistRow key={item.id} item={item} />
                            ))}
                        </ul>
                    </div>
                ))}
            </div>
        </div>
    )
}

function ChecklistRow({ item }: { item: LaunchChecklistItem }) {
    return (
        <li className="rounded-md border border-border px-2.5 py-2">
            <div className="flex items-start gap-2">
                {statusIcon(item.status)}
                <div className="min-w-0">
                    <div className="text-xs font-medium text-foreground">{item.label}</div>
                    <div className="mt-0.5 text-xs leading-5 text-muted-foreground">{item.message}</div>
                </div>
            </div>
        </li>
    )
}

function StatusPill({ status }: { status: LaunchChecklistStatus }) {
    return (
        <span
            className={cn(
                "shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold capitalize",
                status === "pass" && "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300",
                status === "warning" && "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-300",
                status === "blocked" && "border-red-200 bg-red-50 text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300",
                status === "unknown" && "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-800 dark:bg-slate-950/30 dark:text-slate-300",
            )}
        >
            {status}
        </span>
    )
}

function Metric({ label, value }: { label: string; value: string }) {
    return (
        <div className="min-w-0 rounded-md border border-border px-2 py-1.5">
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
            <div className="truncate font-medium text-foreground">{value}</div>
        </div>
    )
}

function groupItems(items: LaunchChecklistItem[]): Map<string, LaunchChecklistItem[]> {
    const grouped = new Map<string, LaunchChecklistItem[]>()
    for (const item of items) {
        grouped.set(item.section, [...(grouped.get(item.section) ?? []), item])
    }
    return grouped
}

function statusIcon(status: LaunchChecklistStatus) {
    const className = cn("mt-0.5 h-3.5 w-3.5 shrink-0", STATUS_STYLES[status])
    if (status === "pass") return <CheckCircle2 className={className} />
    if (status === "blocked") return <AlertCircle className={className} />
    if (status === "warning") return <AlertTriangle className={className} />
    return <CircleHelp className={className} />
}

function formatVolume(volume: Record<string, number> | null): string {
    if (!volume) return "Unknown"
    const entries = Object.entries(volume).filter(([, value]) => value > 0)
    if (!entries.length) return "0"
    return entries.map(([channel, value]) => `${value} ${channel}`).join(", ")
}

function formatCost(cents: number | null): string {
    if (cents === null) return "Unknown"
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100)
}
