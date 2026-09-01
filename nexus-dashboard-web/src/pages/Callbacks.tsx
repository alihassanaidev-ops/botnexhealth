import { useState, useCallback, useEffect, useRef, type ComponentProps } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import {
    PhoneForwarded,
    Search,
    ChevronLeft,
    ChevronRight,
    X,
    CheckCircle2,
    RefreshCcw,
    CircleDot,
} from "lucide-react"
import callbackQueueArt from "@/assets/icons/presentation/callback-queue.png"
import { PageHeader } from "@/components/PageHeader"
import { RevealablePhone } from "@/components/RevealablePhone"
import { UiButton, UiInput, UiSelect, UiSkeleton } from "@/components/foundation/Primitives"
import {
    UiTable as Table,
    UiTableBody as TableBody,
    UiTableCell as TableCell,
    UiTableHead as TableHead,
    UiTableHeader as TableHeader,
    UiTableRow as TableRow,
} from "@/components/foundation/DataTable"
import {
    UiDialog as Dialog,
    UiDialogContent as DialogContent,
    UiDialogHeader as DialogHeader,
    UiDialogTitle as DialogTitle,
} from "@/components/foundation/Overlay"
import { toast } from "sonner"
import { useSSE } from "@/hooks/useSSE"
import { listCallbacks } from "@/lib/callbacks-api"
import { resolveCallback } from "@/lib/calls-api"
import { listWorkflowStatuses } from "@/lib/workflow-status-api"
import { ConversationView } from "@/components/calls/ConversationView"
import { ViewSwitch } from "@/components/calls/shared"
import { DateRangeFilter } from "@/components/DateRangeFilter"
import type { CallbackListItem, CallbacksListResponse, WorkflowStatus } from "@/types"

// ── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 25

type ViewMode = "table" | "conversation"

function Button({ variant = "default", ...props }: Omit<ComponentProps<typeof UiButton>, "variant"> & {
    variant?: "default" | "outline" | "ghost"
}) {
    return <UiButton variant={variant === "default" ? "primary" : variant === "outline" ? "secondary" : "quiet"} {...props} />
}

const Input = UiInput
const Skeleton = UiSkeleton

function Card({ className = "", ...props }: ComponentProps<"section">) {
    return <section className={`overflow-hidden rounded-2xl border border-border/80 bg-card shadow-sm ${className}`.trim()} {...props} />
}

function CardContent({ className = "", ...props }: ComponentProps<"div">) {
    return <div className={className} {...props} />
}

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
                        <CheckCircle2 className="h-5 w-5 text-green-600 dark:text-green-400" />
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
                    <TableCell className="px-4 py-3"><Skeleton className="h-4 w-24" /></TableCell>
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
                {item.booked_appointment_type_name && (
                    <div className="mt-0.5">
                        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-2xs font-medium text-emerald-600 dark:text-emerald-400">
                            Booked: {item.booked_appointment_type_name}
                        </span>
                    </div>
                )}
            </TableCell>

            <TableCell className="whitespace-nowrap px-4 text-sm">
                <RevealablePhone
                    callId={item.call_id}
                    masked={item.phone_masked}
                    available={item.phone_reveal_available}
                />
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
                    <span className="inline-flex items-center gap-1 text-xs text-green-600 dark:text-green-400 font-medium bg-green-500/10 px-2 py-0.5 rounded-full">
                        <CheckCircle2 className="h-3 w-3" /> Resolved
                    </span>
                ) : (
                    <Button
                        variant="outline"
                        size="sm"
                        className="text-xs gap-1"
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
    const { lastEvent } = useSSE()
    const navigate = useNavigate()
    const [searchParams, setSearchParams] = useSearchParams()
    const [data, setData] = useState<CallbacksListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [resolveTarget, setResolveTarget] = useState<CallbackListItem | null>(null)
    const [statuses, setStatuses] = useState<WorkflowStatus[]>([])
    useEffect(() => {
        listWorkflowStatuses().then(setStatuses).catch(() => { /* non-fatal */ })
    }, [])
    const [viewMode, setViewMode] = useState<ViewMode>(
        searchParams.get("view") === "conversation" ? "conversation" : "table"
    )

    function changeView(mode: ViewMode) {
        setViewMode(mode)
        setSearchParams((prev) => {
            if (mode === "conversation") prev.set("view", "conversation")
            else prev.delete("view")
            return prev
        }, { replace: true })
    }

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

    useEffect(() => {
        if (lastEvent?.type !== "callbacks_updated" && lastEvent?.type !== "calls_updated") {
            return
        }
        fetchCallbacks()
    }, [fetchCallbacks, lastEvent])

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
        <div className="ui-page ui-page-stack">
            <PageHeader
                icon={PhoneForwarded}
                art={callbackQueueArt}
                title={
                    <>
                        Callback Queue
                        {!loading && data && (
                            <span className="page-header-count">({total.toLocaleString()})</span>
                        )}
                    </>
                }
                description="Track and manage patient callbacks that need follow-up."
                actions={
                    <Button variant="outline" size="sm" onClick={fetchCallbacks} disabled={loading} className="gap-1.5">
                        <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                }
            />

            {/* Filters */}
            <div className="flex items-center justify-between">
                <div className="flex flex-1 flex-wrap items-center gap-2">
                    <div className="relative w-[180px] shrink-0 lg:w-[250px]">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                        <Input
                            placeholder="Search patient..."
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            className="h-8 w-full pl-8"
                        />
                    </div>

                    <div className="w-[150px] shrink-0">
                        <UiSelect value={resolvedFilter} onChange={(event) => setResolvedFilter(event.target.value)} uiSize="sm" aria-label="Status">
                            <option value="unresolved">Unresolved</option>
                            <option value="resolved">Resolved</option>
                            <option value="all">All</option>
                        </UiSelect>
                    </div>

                    <div className="w-[130px] shrink-0">
                        <UiSelect value={sortOrder} onChange={(event) => setSortOrder(event.target.value as "oldest" | "newest")} uiSize="sm" aria-label="Sort">
                            <option value="oldest">Oldest first</option>
                            <option value="newest">Newest first</option>
                        </UiSelect>
                    </div>

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
                            className="px-2 lg:px-3 text-muted-foreground"
                        >
                            Reset
                            <X className="ml-2 h-4 w-4" />
                        </Button>
                    )}
                </div>
                <ViewSwitch value={viewMode} onChange={changeView} />
            </div>

            {/* Conversation (inbox) view */}
            {viewMode === "conversation" ? (
                <ConversationView
                    items={(data?.items ?? []).map((it) => ({
                        id: it.call_id,
                        name: it.contact_name ?? it.contact?.full_name ?? null,
                        date: it.call_date,
                        time: it.call_time,
                        summary: it.summary,
                        needsCallback: !it.callback_resolved,
                        status: it.workflow_status,
                    }))}
                    loading={loading}
                    total={total}
                    page={page}
                    pageCount={pageCount}
                    from={from}
                    to={to}
                    hasFilters={hasFilters}
                    onPageChange={setPage}
                    onResolved={fetchCallbacks}
                    statuses={statuses}
                    title="Callbacks"
                    emptyTitle={resolvedFilter === "unresolved" ? "No pending callbacks" : "No callbacks found"}
                    emptyHint={
                        resolvedFilter === "unresolved"
                            ? "All callbacks have been resolved. Great work!"
                            : "Callbacks will appear here when calls need follow-up."
                    }
                />
            ) : (
            /* Table */
            <Card>
                <CardContent className="p-0">
                    <div className="overflow-x-auto">
                        <Table className="w-full text-sm">
                            <TableHeader className="border-b border-border bg-muted">
                                <TableRow>
                                    <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide w-10">Status</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">Patient</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Phone</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Date & Time</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Duration</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">Summary</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Next Action</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">Action</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {loading ? (
                                    <SkeletonRows />
                                ) : !data || data.items.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={8} className="px-4 py-16 text-center">
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

                    {/* Footer: result count (left) + pagination (right) */}
                    {!loading && total > 0 && (
                        <div className="flex flex-col gap-3 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                            <p className="text-sm text-muted-foreground">
                                Showing <span className="font-medium text-foreground">{from}–{to}</span> of{" "}
                                <span className="font-medium text-foreground">{total.toLocaleString()}</span> callbacks
                            </p>
                            {pageCount > 1 && (
                                <div className="flex items-center gap-2">
                                    <span className="mr-1 text-sm tabular-nums text-muted-foreground">
                                        Page {page + 1} of {pageCount}
                                    </span>
                                    <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)} className="gap-1">
                                        <ChevronLeft className="h-4 w-4" /> Previous
                                    </Button>
                                    <Button variant="outline" size="sm" disabled={page >= pageCount - 1} onClick={() => setPage((p) => p + 1)} className="gap-1">
                                        Next <ChevronRight className="h-4 w-4" />
                                    </Button>
                                </div>
                            )}
                        </div>
                    )}
                </CardContent>
            </Card>
            )}

            {/* Resolve dialog (table view) */}
            <ResolveDialog
                callbackItem={resolveTarget}
                onClose={() => setResolveTarget(null)}
                onResolved={fetchCallbacks}
            />
        </div>
    )
}
