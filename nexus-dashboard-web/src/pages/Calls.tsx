import { useState, useCallback, useEffect, useRef } from "react"
import {
    Phone,
    PhoneIncoming,
    PhoneOutgoing,
    Search,
    ChevronLeft,
    ChevronRight,
    X,
    UserPlus,
    AlertCircle,
    CreditCard,
    User,
} from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import { listCalls } from "@/lib/calls-api"
import type { CallRecord, CallsListResponse } from "@/types"

// ── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 25

const STATUS_OPTIONS = [
    { value: "booked", label: "Booked" },
    { value: "needs_follow_up", label: "Needs Follow-up" },
    { value: "emergency", label: "Emergency" },
    { value: "no_action_needed", label: "No Action Needed" },
    { value: "cancelled", label: "Cancelled" },
    { value: "rescheduled", label: "Rescheduled" },
]

const DIRECTION_OPTIONS = [
    { value: "inbound", label: "Inbound" },
    { value: "outbound", label: "Outbound" },
]

// ── Style helpers ─────────────────────────────────────────────────────────────

function statusBadge(status: string | null) {
    const map: Record<string, string> = {
        booked: "bg-green-100 text-green-800 border border-green-200",
        needs_follow_up: "bg-amber-100 text-amber-800 border border-amber-200",
        emergency: "bg-red-100 text-red-800 border border-red-200",
        cancelled: "bg-zinc-100 text-zinc-600 border border-zinc-200",
        no_action_needed: "bg-zinc-100 text-zinc-600 border border-zinc-200",
        rescheduled: "bg-blue-100 text-blue-800 border border-blue-200",
    }
    const cls = map[status ?? ""] ?? "bg-zinc-100 text-zinc-600 border border-zinc-200"
    const label = STATUS_OPTIONS.find((o) => o.value === status)?.label ?? status ?? "—"
    return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>{label}</span>
}

function sentimentBadge(sentiment: string | null) {
    if (!sentiment) return <span className="text-xs text-muted-foreground">—</span>
    const map: Record<string, string> = {
        Positive: "bg-green-100 text-green-700",
        Negative: "bg-red-100 text-red-700",
        Neutral: "bg-zinc-100 text-zinc-600",
    }
    const cls = map[sentiment] ?? "bg-zinc-100 text-zinc-600"
    return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>{sentiment}</span>
}

function formatDuration(seconds: number | null): string {
    if (seconds === null) return "—"
    if (seconds < 60) return `${seconds}s`
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`
    const h = Math.floor(m / 60)
    const rem = m % 60
    return rem > 0 ? `${h}h ${rem}m` : `${h}h`
}

function formatDateTime(dateStr: string | null, timeStr: string | null): string {
    if (!dateStr) return "—"
    const d = new Date(dateStr)
    const datePart = d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
    if (!timeStr) return datePart
    const [h, m] = timeStr.split(":")
    const hour = parseInt(h, 10)
    const ampm = hour >= 12 ? "PM" : "AM"
    const h12 = hour % 12 || 12
    return `${datePart} · ${h12}:${m} ${ampm}`
}

// ── Call Detail Dialog ────────────────────────────────────────────────────────

interface CallDetailProps {
    call: CallRecord | null
    onClose: () => void
}

function CallDetailDialog({ call, onClose }: CallDetailProps) {
    if (!call) return null
    return (
        <Dialog open={!!call} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        {call.call_direction === "inbound" ? (
                            <PhoneIncoming className="h-4 w-4 text-blue-500" />
                        ) : (
                            <PhoneOutgoing className="h-4 w-4 text-purple-500" />
                        )}
                        Call Detail
                    </DialogTitle>
                </DialogHeader>
                <div className="space-y-4 text-sm">
                    {/* Contact */}
                    <div className="flex items-center gap-2">
                        <User className="h-4 w-4 text-muted-foreground shrink-0" />
                        <span className="font-medium">
                            {call.contact?.full_name ?? <span className="text-muted-foreground">Unknown caller</span>}
                        </span>
                    </div>

                    {/* Meta row */}
                    <div className="flex flex-wrap gap-2">
                        {statusBadge(call.call_status)}
                        {sentimentBadge(call.patient_sentiment)}
                        {call.is_new_patient && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 text-indigo-700 px-2 py-0.5 text-xs font-medium border border-indigo-200">
                                <UserPlus className="h-3 w-3" /> New Patient
                            </span>
                        )}
                        {call.is_complaint && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-red-100 text-red-700 px-2 py-0.5 text-xs font-medium border border-red-200">
                                <AlertCircle className="h-3 w-3" /> Complaint
                            </span>
                        )}
                        {call.is_insurance_billing && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-violet-100 text-violet-700 px-2 py-0.5 text-xs font-medium border border-violet-200">
                                <CreditCard className="h-3 w-3" /> Insurance
                            </span>
                        )}
                    </div>

                    {/* Date & duration */}
                    <div className="grid grid-cols-2 gap-2 rounded-lg border bg-muted/40 p-3 text-xs">
                        <div>
                            <p className="text-muted-foreground">Date & Time</p>
                            <p className="font-medium mt-0.5">{formatDateTime(call.call_date, call.call_time)}</p>
                        </div>
                        <div>
                            <p className="text-muted-foreground">Duration</p>
                            <p className="font-medium mt-0.5">{formatDuration(call.call_duration_seconds)}</p>
                        </div>
                    </div>

                    {/* Summary */}
                    {call.summary && (
                        <div>
                            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">Summary</p>
                            <p className="text-sm leading-relaxed rounded-lg border bg-muted/30 p-3">{call.summary}</p>
                        </div>
                    )}

                    {/* Next action */}
                    {call.next_action && (
                        <div>
                            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">Appointment Detail</p>
                            <p className="text-sm leading-relaxed rounded-lg border bg-muted/30 p-3">{call.next_action}</p>
                        </div>
                    )}
                </div>
            </DialogContent>
        </Dialog>
    )
}

// ── Skeleton row ─────────────────────────────────────────────────────────────

function SkeletonRows() {
    return (
        <>
            {Array.from({ length: 8 }).map((_, i) => (
                <tr key={i} className="border-b border-border/50">
                    <td className="px-4 py-3"><Skeleton className="h-4 w-28" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-24" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16 rounded-full" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20 rounded-full" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-14 rounded-full" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-10" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-48" /></td>
                </tr>
            ))}
        </>
    )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function Calls() {
    const [data, setData] = useState<CallsListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [selected, setSelected] = useState<CallRecord | null>(null)

    // Filters
    const [search, setSearch] = useState("")
    const [statusFilter, setStatusFilter] = useState("")
    const [directionFilter, setDirectionFilter] = useState("")
    const [dateFrom, setDateFrom] = useState("")
    const [dateTo, setDateTo] = useState("")

    // Pagination
    const [page, setPage] = useState(0)

    // Debounce search
    const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    const [debouncedSearch, setDebouncedSearch] = useState("")
    useEffect(() => {
        if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
        searchTimerRef.current = setTimeout(() => setDebouncedSearch(search), 400)
        return () => { if (searchTimerRef.current) clearTimeout(searchTimerRef.current) }
    }, [search])

    // Reset to page 0 on any filter change
    useEffect(() => { setPage(0) }, [debouncedSearch, statusFilter, directionFilter, dateFrom, dateTo])

    const fetchCalls = useCallback(async () => {
        setLoading(true)
        try {
            const result = await listCalls({
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
                status: statusFilter || undefined,
                direction: directionFilter || undefined,
                search: debouncedSearch || undefined,
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
            })
            setData(result)
        } catch (err: unknown) {
            toast.error(err instanceof Error ? err.message : "Failed to load calls")
        } finally {
            setLoading(false)
        }
    }, [page, statusFilter, directionFilter, debouncedSearch, dateFrom, dateTo])

    useEffect(() => { fetchCalls() }, [fetchCalls])

    const hasFilters = !!(statusFilter || directionFilter || dateFrom || dateTo || search)

    function clearFilters() {
        setSearch("")
        setStatusFilter("")
        setDirectionFilter("")
        setDateFrom("")
        setDateTo("")
    }

    const total = data?.total ?? 0
    const pageCount = Math.ceil(total / PAGE_SIZE)
    const from = total === 0 ? 0 : page * PAGE_SIZE + 1
    const to = Math.min((page + 1) * PAGE_SIZE, total)

    return (
        <div className="flex-1 space-y-6 p-8 pt-6">
            {/* Page header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <Phone className="h-7 w-7" />
                        Calls
                    </h2>
                    <p className="text-muted-foreground mt-1">
                        Browse and review all patient calls handled by your voice agent.
                    </p>
                </div>
                {!loading && data && (
                    <div className="text-right">
                        <p className="text-2xl font-bold tabular-nums">{total.toLocaleString()}</p>
                        <p className="text-xs text-muted-foreground">total calls</p>
                    </div>
                )}
            </div>

            {/* Filters */}
            <Card>
                <CardContent className="pt-4 pb-4">
                    <div className="flex flex-wrap gap-3 items-end">
                        {/* Search */}
                        <div className="relative flex-1 min-w-[180px] max-w-xs">
                            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" />
                            <Input
                                placeholder="Search by patient name…"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                className="pl-9"
                            />
                        </div>

                        {/* Status */}
                        <Select value={statusFilter || "all"} onValueChange={(v) => setStatusFilter(v === "all" ? "" : v)}>
                            <SelectTrigger className="w-[170px]">
                                <SelectValue placeholder="All statuses" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All statuses</SelectItem>
                                {STATUS_OPTIONS.map((o) => (
                                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>

                        {/* Direction */}
                        <Select value={directionFilter || "all"} onValueChange={(v) => setDirectionFilter(v === "all" ? "" : v)}>
                            <SelectTrigger className="w-[140px]">
                                <SelectValue placeholder="All directions" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All directions</SelectItem>
                                {DIRECTION_OPTIONS.map((o) => (
                                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>

                        {/* Date from */}
                        <div className="flex flex-col gap-1">
                            <span className="text-xs text-muted-foreground pl-0.5">From</span>
                            <Input
                                type="date"
                                value={dateFrom}
                                onChange={(e) => setDateFrom(e.target.value)}
                                className="w-[145px]"
                            />
                        </div>

                        {/* Date to */}
                        <div className="flex flex-col gap-1">
                            <span className="text-xs text-muted-foreground pl-0.5">To</span>
                            <Input
                                type="date"
                                value={dateTo}
                                onChange={(e) => setDateTo(e.target.value)}
                                className="w-[145px]"
                            />
                        </div>

                        {/* Clear */}
                        {hasFilters && (
                            <Button variant="ghost" size="sm" onClick={clearFilters} className="gap-1.5 self-end mb-0">
                                <X className="h-3.5 w-3.5" />
                                Clear
                            </Button>
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* Table */}
            <Card>
                <CardHeader className="pb-0">
                    <CardTitle className="text-base font-medium text-muted-foreground">
                        {loading ? (
                            <Skeleton className="h-4 w-40" />
                        ) : (
                            total > 0
                                ? `Showing ${from}–${to} of ${total.toLocaleString()} calls`
                                : "No calls found"
                        )}
                    </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-border bg-muted/40">
                                    <th className="px-4 py-3 text-left font-medium text-muted-foreground whitespace-nowrap">Date & Time</th>
                                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Patient</th>
                                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Direction</th>
                                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Status</th>
                                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Sentiment</th>
                                    <th className="px-4 py-3 text-left font-medium text-muted-foreground whitespace-nowrap">Duration</th>
                                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Summary</th>
                                </tr>
                            </thead>
                            <tbody>
                                {loading ? (
                                    <SkeletonRows />
                                ) : !data || data.items.length === 0 ? (
                                    <tr>
                                        <td colSpan={7} className="px-4 py-12 text-center text-muted-foreground">
                                            <Phone className="h-10 w-10 mx-auto mb-3 opacity-20" />
                                            <p className="font-medium">No calls yet</p>
                                            <p className="text-xs mt-1">
                                                {hasFilters
                                                    ? "No calls match your current filters."
                                                    : "Calls will appear here once your voice agent starts taking calls."}
                                            </p>
                                        </td>
                                    </tr>
                                ) : (
                                    data.items.map((call) => (
                                        <CallRow
                                            key={call.id}
                                            call={call}
                                            onClick={() => setSelected(call)}
                                        />
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>
                </CardContent>
            </Card>

            {/* Pagination */}
            {!loading && pageCount > 1 && (
                <div className="flex items-center justify-between">
                    <p className="text-sm text-muted-foreground">
                        Page {page + 1} of {pageCount}
                    </p>
                    <div className="flex gap-2">
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={page === 0}
                            onClick={() => setPage((p) => p - 1)}
                            className="gap-1"
                        >
                            <ChevronLeft className="h-4 w-4" />
                            Previous
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={page >= pageCount - 1}
                            onClick={() => setPage((p) => p + 1)}
                            className="gap-1"
                        >
                            Next
                            <ChevronRight className="h-4 w-4" />
                        </Button>
                    </div>
                </div>
            )}

            {/* Detail dialog */}
            <CallDetailDialog call={selected} onClose={() => setSelected(null)} />
        </div>
    )
}

// ── Call row ──────────────────────────────────────────────────────────────────

interface CallRowProps {
    call: CallRecord
    onClick: () => void
}

function CallRow({ call, onClick }: CallRowProps) {
    const hasSummary = !!call.summary

    return (
        <tr
            className="border-b border-border/50 hover:bg-muted/30 cursor-pointer transition-colors"
            onClick={onClick}
        >
            {/* Date & Time */}
            <td className="px-4 py-3 whitespace-nowrap text-xs text-muted-foreground">
                {formatDateTime(call.call_date, call.call_time)}
            </td>

            {/* Patient */}
            <td className="px-4 py-3">
                <div className="flex items-center gap-1.5">
                    <span className={call.contact?.full_name ? "font-medium" : "text-muted-foreground"}>
                        {call.contact?.full_name ?? "Unknown"}
                    </span>
                    {call.is_new_patient && (
                        <UserPlus className="h-3.5 w-3.5 text-indigo-500 shrink-0" aria-label="New patient" />
                    )}
                    {call.is_complaint && (
                        <AlertCircle className="h-3.5 w-3.5 text-red-500 shrink-0" aria-label="Complaint" />
                    )}
                    {call.is_insurance_billing && (
                        <CreditCard className="h-3.5 w-3.5 text-violet-500 shrink-0" aria-label="Insurance / billing" />
                    )}
                </div>
            </td>

            {/* Direction */}
            <td className="px-4 py-3">
                {call.call_direction === "inbound" ? (
                    <span className="inline-flex items-center gap-1 text-xs text-blue-600 font-medium">
                        <PhoneIncoming className="h-3.5 w-3.5" />
                        Inbound
                    </span>
                ) : call.call_direction === "outbound" ? (
                    <span className="inline-flex items-center gap-1 text-xs text-purple-600 font-medium">
                        <PhoneOutgoing className="h-3.5 w-3.5" />
                        Outbound
                    </span>
                ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                )}
            </td>

            {/* Status */}
            <td className="px-4 py-3">{statusBadge(call.call_status)}</td>

            {/* Sentiment */}
            <td className="px-4 py-3">{sentimentBadge(call.patient_sentiment)}</td>

            {/* Duration */}
            <td className="px-4 py-3 text-xs text-muted-foreground tabular-nums whitespace-nowrap">
                {formatDuration(call.call_duration_seconds)}
            </td>

            {/* Summary */}
            <td className="px-4 py-3 max-w-[320px]">
                {hasSummary ? (
                    <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">
                        {call.summary}
                    </p>
                ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                )}
            </td>
        </tr>
    )
}
