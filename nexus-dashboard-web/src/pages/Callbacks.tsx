import { useState, useCallback, useEffect, useRef } from "react"
import { useNavigate } from "react-router-dom"
import {
    PhoneForwarded,
    CalendarIcon,
    Search,
    ChevronLeft,
    ChevronRight,
    X,
    CheckCircle2,
    RefreshCcw,
    Clock,
    CircleDot,
} from "lucide-react"
import { format } from "date-fns"
import type { DateRange } from "react-day-picker"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Calendar } from "@/components/ui/calendar"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import { listCallbacks } from "@/lib/callbacks-api"
import { resolveCallback } from "@/lib/calls-api"
import { cn } from "@/lib/utils"
import type { CallbackListItem, CallbacksListResponse } from "@/types"

// ── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 25
const POLL_INTERVAL_MS = 30_000

// ── Helpers ──────────────────────────────────────────────────────────────────

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

function parseDateString(value: string): Date | undefined {
    if (!value) return undefined
    const [year, month, day] = value.split("-").map(Number)
    if (!year || !month || !day) return undefined
    const parsed = new Date(year, month - 1, day)
    return Number.isNaN(parsed.getTime()) ? undefined : parsed
}

function formatDateParam(value: Date): string {
    return format(value, "yyyy-MM-dd")
}

// ── Date Range Filter ────────────────────────────────────────────────────────

interface DateRangeFilterProps {
    from: string
    to: string
    onChange: (next: { from: string; to: string }) => void
}

function DateRangeFilter({ from, to, onChange }: DateRangeFilterProps) {
    const fromDate = parseDateString(from)
    const toDate = parseDateString(to)
    const selectedRange: DateRange | undefined = (fromDate || toDate)
        ? { from: fromDate, to: toDate }
        : undefined

    const label = selectedRange?.from
        ? selectedRange.to
            ? `${format(selectedRange.from, "MMM d, yyyy")} - ${format(selectedRange.to, "MMM d, yyyy")}`
            : format(selectedRange.from, "MMM d, yyyy")
        : "Date range"

    return (
        <Popover>
            <PopoverTrigger asChild>
                <Button
                    variant="outline"
                    size="sm"
                    className="h-8 w-[215px] justify-start text-left font-normal"
                >
                    <CalendarIcon className="mr-2 h-4 w-4" />
                    <span className={cn(!selectedRange?.from && "text-muted-foreground")}>{label}</span>
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="start">
                <Calendar
                    mode="range"
                    numberOfMonths={2}
                    selected={selectedRange}
                    onSelect={(range) => onChange({
                        from: range?.from ? formatDateParam(range.from) : "",
                        to: range?.to ? formatDateParam(range.to) : "",
                    })}
                    initialFocus
                />
                {selectedRange?.from && (
                    <div className="border-t p-2">
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2 text-xs"
                            onClick={() => onChange({ from: "", to: "" })}
                        >
                            Clear
                        </Button>
                    </div>
                )}
            </PopoverContent>
        </Popover>
    )
}

// ── Resolve Dialog ───────────────────────────────────────────────────────────

interface ResolveDialogProps {
    callbackItem: CallbackListItem | null
    onClose: () => void
    onResolved: () => void
}

function ResolveDialog({ callbackItem, onClose, onResolved }: ResolveDialogProps) {
    const [note, setNote] = useState("")
    const [resolving, setResolving] = useState(false)

    useEffect(() => {
        if (!callbackItem) setNote("")
    }, [callbackItem])

    async function handleResolve() {
        if (!callbackItem) return
        setResolving(true)
        try {
            await resolveCallback(callbackItem.call_id, note || undefined)
            toast.success("Callback marked as resolved")
            onResolved()
            onClose()
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to resolve callback")
        } finally {
            setResolving(false)
        }
    }

    return (
        <Dialog open={!!callbackItem} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="max-w-md">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <CheckCircle2 className="h-5 w-5 text-green-600" />
                        Resolve Callback
                    </DialogTitle>
                </DialogHeader>
                <div className="space-y-4">
                    <div className="rounded-lg border bg-muted p-3 text-sm space-y-1">
                        <p className="font-medium">
                            {callbackItem?.contact_name ?? callbackItem?.contact?.full_name ?? "Unknown caller"}
                        </p>
                        <p className="text-xs text-muted-foreground">
                            {formatDateTime(callbackItem?.call_date ?? null, callbackItem?.call_time ?? null)}
                        </p>
                        {callbackItem?.summary && (
                            <p className="text-xs text-muted-foreground mt-2 line-clamp-3">{callbackItem.summary}</p>
                        )}
                    </div>
                    <div>
                        <label className="text-xs font-medium text-muted-foreground mb-1 block">
                            Resolution note (optional)
                        </label>
                        <Input
                            placeholder="e.g. Spoke with patient, rescheduled appointment…"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            className="text-sm"
                        />
                    </div>
                    <div className="flex justify-end gap-2">
                        <Button variant="outline" size="sm" onClick={onClose}>
                            Cancel
                        </Button>
                        <Button
                            size="sm"
                            className="gap-1.5"
                            onClick={handleResolve}
                            disabled={resolving}
                        >
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            {resolving ? "Resolving…" : "Mark Resolved"}
                        </Button>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    )
}

// ── Skeleton rows ────────────────────────────────────────────────────────────

function SkeletonRows() {
    return (
        <>
            {Array.from({ length: 8 }).map((_, i) => (
                <TableRow key={i}>
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-6" /></TableCell>
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-28" /></TableCell>
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-32" /></TableCell>
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-12" /></TableCell>
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-48" /></TableCell>
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-40" /></TableCell>
                    <TableCell className="px-4 py-3"><Skeleton className="h-6 w-20" /></TableCell>
                </TableRow>
            ))}
        </>
    )
}

// ── Callback Row ─────────────────────────────────────────────────────────────

interface CallbackRowProps {
    item: CallbackListItem
    onResolve: () => void
    onClick: () => void
}

function CallbackRow({ item, onResolve, onClick }: CallbackRowProps) {
    return (
        <TableRow className="cursor-pointer hover:bg-muted transition-colors" onClick={onClick}>
            <TableCell className="px-4">
                {item.callback_resolved ? (
                    <CheckCircle2 className="h-4 w-4 text-green-500" />
                ) : (
                    <CircleDot className="h-4 w-4 text-amber-500" />
                )}
            </TableCell>

            <TableCell className="px-4">
                <span className={item.contact_name || item.contact?.full_name ? "font-medium" : "text-muted-foreground"}>
                    {item.contact_name ?? item.contact?.full_name ?? "Unknown"}
                </span>
            </TableCell>

            <TableCell className="whitespace-nowrap text-muted-foreground px-4">
                {formatDateTime(item.call_date, item.call_time)}
            </TableCell>

            <TableCell className="text-muted-foreground tabular-nums whitespace-nowrap px-4">
                {formatDuration(item.call_duration_seconds)}
            </TableCell>

            <TableCell className="max-w-[250px] px-4">
                {item.summary ? (
                    <p className="text-muted-foreground line-clamp-2 leading-relaxed text-xs">{item.summary}</p>
                ) : (
                    <span className="text-muted-foreground">—</span>
                )}
            </TableCell>

            <TableCell className="max-w-[200px] px-4">
                {item.next_action ? (
                    <p className="text-muted-foreground line-clamp-2 leading-relaxed text-xs">{item.next_action}</p>
                ) : (
                    <span className="text-muted-foreground">—</span>
                )}
            </TableCell>

            <TableCell className="px-4">
                {item.callback_resolved ? (
                    <span className="inline-flex items-center gap-1 text-xs text-green-600 font-medium bg-green-500/10 px-2 py-0.5 rounded-full">
                        <CheckCircle2 className="h-3 w-3" /> Resolved
                    </span>
                ) : (
                    <Button
                        variant="outline"
                        size="sm"
                        className="h-7 text-xs gap-1"
                        onClick={(e) => { e.stopPropagation(); onResolve() }}
                    >
                        <CheckCircle2 className="h-3 w-3" /> Resolve
                    </Button>
                )}
            </TableCell>
        </TableRow>
    )
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function Callbacks() {
    const navigate = useNavigate()
    const [data, setData] = useState<CallbacksListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [resolveTarget, setResolveTarget] = useState<CallbackListItem | null>(null)

    // Filters
    const [search, setSearch] = useState("")
    const [resolvedFilter, setResolvedFilter] = useState<string>("unresolved")
    const [dateFrom, setDateFrom] = useState("")
    const [dateTo, setDateTo] = useState("")
    const [sortOrder, setSortOrder] = useState<"oldest" | "newest">("oldest")

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

    // Reset page on filter change
    useEffect(() => { setPage(0) }, [debouncedSearch, resolvedFilter, dateFrom, dateTo, sortOrder])

    const fetchCallbacks = useCallback(async () => {
        setLoading(true)
        try {
            const resolved = resolvedFilter === "all" ? undefined
                : resolvedFilter === "resolved" ? true : false
            const result = await listCallbacks({
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
                resolved,
                search: debouncedSearch || undefined,
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
                sort: sortOrder,
            })
            setData(result)
        } catch (err: unknown) {
            toast.error(err instanceof Error ? err.message : "Failed to load callbacks")
        } finally {
            setLoading(false)
        }
    }, [page, resolvedFilter, debouncedSearch, dateFrom, dateTo, sortOrder])

    useEffect(() => { fetchCallbacks() }, [fetchCallbacks])

    // 30-second auto-poll
    useEffect(() => {
        const id = setInterval(fetchCallbacks, POLL_INTERVAL_MS)
        return () => clearInterval(id)
    }, [fetchCallbacks])

    const hasFilters = !!(resolvedFilter !== "unresolved" || dateFrom || dateTo || search)

    function clearFilters() {
        setSearch("")
        setResolvedFilter("unresolved")
        setDateFrom("")
        setDateTo("")
        setSortOrder("oldest")
    }

    const total = data?.total ?? 0
    const pageCount = Math.ceil(total / PAGE_SIZE)
    const from = total === 0 ? 0 : page * PAGE_SIZE + 1
    const to = Math.min((page + 1) * PAGE_SIZE, total)

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
            {/* Page header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <PhoneForwarded className="h-7 w-7" />
                        Callback Queue
                    </h2>
                    <p className="text-muted-foreground mt-1">
                        Track and manage patient callbacks that need follow-up.
                    </p>
                </div>
                <div className="flex items-center gap-3">
                    {!loading && data && (
                        <div className="text-right">
                            <p className="text-2xl font-bold tabular-nums">{total.toLocaleString()}</p>
                            <p className="text-xs text-muted-foreground">
                                {resolvedFilter === "resolved" ? "resolved" : resolvedFilter === "all" ? "total" : "pending"}
                            </p>
                        </div>
                    )}
                    <Button variant="outline" size="sm" onClick={fetchCallbacks} disabled={loading} className="gap-1.5">
                        <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                </div>
            </div>

            {/* Filters */}
            <div className="flex items-center justify-between">
                <div className="flex flex-1 items-center space-x-2 overflow-x-auto pb-2 -mb-2">
                    <div className="relative">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                        <Input
                            placeholder="Search patient..."
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            className="h-8 pl-8 w-[150px] lg:w-[250px]"
                        />
                    </div>

                    <Select value={resolvedFilter} onValueChange={setResolvedFilter}>
                        <SelectTrigger className="h-8 w-[150px]">
                            <SelectValue placeholder="Status" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="unresolved">
                                <span className="flex items-center gap-1.5">
                                    <CircleDot className="h-3 w-3 text-amber-500" /> Unresolved
                                </span>
                            </SelectItem>
                            <SelectItem value="resolved">
                                <span className="flex items-center gap-1.5">
                                    <CheckCircle2 className="h-3 w-3 text-green-500" /> Resolved
                                </span>
                            </SelectItem>
                            <SelectItem value="all">All</SelectItem>
                        </SelectContent>
                    </Select>

                    <Select value={sortOrder} onValueChange={(v) => setSortOrder(v as "oldest" | "newest")}>
                        <SelectTrigger className="h-8 w-[130px]">
                            <SelectValue placeholder="Sort" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="oldest">
                                <span className="flex items-center gap-1.5">
                                    <Clock className="h-3 w-3" /> Oldest first
                                </span>
                            </SelectItem>
                            <SelectItem value="newest">
                                <span className="flex items-center gap-1.5">
                                    <Clock className="h-3 w-3" /> Newest first
                                </span>
                            </SelectItem>
                        </SelectContent>
                    </Select>

                    <DateRangeFilter
                        from={dateFrom}
                        to={dateTo}
                        onChange={({ from, to }) => {
                            setDateFrom(from)
                            setDateTo(to)
                        }}
                    />

                    {hasFilters && (
                        <Button
                            variant="ghost"
                            onClick={clearFilters}
                            className="h-8 px-2 lg:px-3 text-muted-foreground"
                        >
                            Reset
                            <X className="ml-2 h-4 w-4" />
                        </Button>
                    )}
                </div>
            </div>

            {/* Table */}
            <Card>
                <CardHeader className="pb-0">
                    <CardTitle className="text-base font-medium text-muted-foreground">
                        {loading ? (
                            <Skeleton className="h-4 w-40" />
                        ) : (
                            total > 0
                                ? `Showing ${from}–${to} of ${total.toLocaleString()} callbacks`
                                : "No callbacks found"
                        )}
                    </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                    <div className="overflow-x-auto">
                        <Table className="w-full text-sm">
                            <TableHeader className="border-b border-border bg-muted">
                                <TableRow>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide w-10">Status</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Patient</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Date & Time</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Duration</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Summary</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Next Action</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Action</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {loading ? (
                                    <SkeletonRows />
                                ) : !data || data.items.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={7} className="px-4 py-16 text-center">
                                            <div className="flex flex-col items-center gap-3 text-muted-foreground">
                                                <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center">
                                                    <PhoneForwarded className="h-6 w-6 opacity-40" />
                                                </div>
                                                <div>
                                                    <p className="font-medium text-sm text-foreground/70">
                                                        {resolvedFilter === "unresolved"
                                                            ? "No pending callbacks"
                                                            : "No callbacks found"}
                                                    </p>
                                                    <p className="text-xs mt-0.5">
                                                        {hasFilters
                                                            ? "Try adjusting or clearing your filters."
                                                            : resolvedFilter === "unresolved"
                                                                ? "All callbacks have been resolved. Great work!"
                                                                : "Callbacks will appear here when calls need follow-up."}
                                                    </p>
                                                </div>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    data.items.map((item) => (
                                        <CallbackRow
                                            key={item.call_id}
                                            item={item}
                                            onResolve={() => setResolveTarget(item)}
                                            onClick={() => navigate(`/calls?detail=${item.call_id}`)}
                                        />
                                    ))
                                )}
                            </TableBody>
                        </Table>
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
                        <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)} className="gap-1">
                            <ChevronLeft className="h-4 w-4" /> Previous
                        </Button>
                        <Button variant="outline" size="sm" disabled={page >= pageCount - 1} onClick={() => setPage((p) => p + 1)} className="gap-1">
                            Next <ChevronRight className="h-4 w-4" />
                        </Button>
                    </div>
                </div>
            )}

            {/* Resolve dialog */}
            <ResolveDialog
                callbackItem={resolveTarget}
                onClose={() => setResolveTarget(null)}
                onResolved={fetchCallbacks}
            />
        </div>
    )
}
