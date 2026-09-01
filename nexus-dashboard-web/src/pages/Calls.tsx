import { useState, useCallback, useEffect, useRef, type ComponentProps } from "react"
import { useSearchParams } from "react-router-dom"
import {
    Phone,
    PhoneIncoming,
    PhoneOutgoing,
    Search,
    ChevronLeft,
    ChevronRight,
    X,
    UserPlus,
    CheckCircle2,
    RefreshCcw,
    PlusCircle,
} from "lucide-react"
import callsArt from "@/assets/icons/presentation/calls.png"
import { PageHeader } from "@/components/PageHeader"
import { RevealablePhone } from "@/components/RevealablePhone"
import { DateRangeFilter } from "@/components/DateRangeFilter"
import { UiBadge, UiButton, UiInput, UiSelect, UiSkeleton } from "@/components/foundation/Primitives"
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
import { getCall, listCalls, resolveCallback } from "@/lib/calls-api"
import { ConversationView } from "@/components/calls/ConversationView"
import {
    CustomFieldsSection,
    RecordingSection,
    TranscriptSection,
    TagBadge,
    SentimentBadge,
    StatusBadge,
    StatusSelect,
    ViewSwitch,
} from "@/components/calls/shared"
import { formatDateTime, formatDuration, getInitials } from "@/components/calls/format"
import { listWorkflowStatuses, assignCallStatus } from "@/lib/workflow-status-api"
import { STATUS_OPTIONS, DIRECTION_OPTIONS } from "@/lib/constants"
import type { CallRecord, CallDetail, CallsListResponse, WorkflowStatus } from "@/types"
import "./calls.css"

// ── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 25

type ViewMode = "table" | "conversation"

function Button({ variant = "default", ...props }: Omit<ComponentProps<typeof UiButton>, "variant"> & {
    variant?: "default" | "outline" | "ghost" | "secondary"
}) {
    const mappedVariant = variant === "default"
        ? "primary"
        : variant === "outline" || variant === "secondary"
            ? "secondary"
            : "quiet"
    return <UiButton variant={mappedVariant} {...props} />
}

function Badge({ variant = "secondary", ...props }: Omit<ComponentProps<typeof UiBadge>, "tone"> & {
    variant?: "secondary" | "destructive" | "outline"
}) {
    return <UiBadge tone={variant === "destructive" ? "danger" : "neutral"} {...props} />
}

const Input = UiInput
const Skeleton = UiSkeleton

function Separator({ orientation = "horizontal", className = "" }: {
    orientation?: "horizontal" | "vertical"
    className?: string
}) {
    return <span aria-hidden="true" className={`${orientation === "vertical" ? "inline-block w-px bg-border" : "block h-px bg-border"} ${className}`.trim()} />
}

function Card({ className = "", ...props }: ComponentProps<"section">) {
    return <section className={`calls-surface ${className}`.trim()} {...props} />
}

function CardContent({ className = "", ...props }: ComponentProps<"div">) {
    return <div className={className} {...props} />
}

// ── Tag filter toggle ─────────────────────────────────────────────────────────

interface CallsFacetedFilterProps {
    title?: string
    options: { label: string; value: string; color?: string }[]
    selectedValues: Set<string>
    onSelectedChange: (values: Set<string>) => void
}

function CallsFacetedFilter({
    title,
    options,
    selectedValues,
    onSelectedChange,
}: CallsFacetedFilterProps) {
    return (
        <details className="calls-filter group">
            <summary className="calls-filter-trigger ui-button ui-button-secondary ui-button-sm">
                    <PlusCircle className="mr-2 h-4 w-4" />
                    {title}
                    {selectedValues.size > 0 && (
                        <>
                            <Separator orientation="vertical" className="mx-2 h-4" />
                            <Badge
                                variant="secondary"
                                className="rounded-sm px-1 font-normal lg:hidden"
                            >
                                {selectedValues.size}
                            </Badge>
                            <div className="hidden space-x-1 lg:flex items-center">
                                {selectedValues.size > 2 ? (
                                    <Badge
                                        variant="secondary"
                                        className="rounded-sm px-1 font-normal"
                                    >
                                        {selectedValues.size} selected
                                    </Badge>
                                ) : (
                                    options
                                        .filter((option) => selectedValues.has(option.value))
                                        .map((option) => (
                                            <Badge
                                                variant="secondary"
                                                key={option.value}
                                                className="rounded-sm px-1 font-normal"
                                            >
                                                {option.label}
                                            </Badge>
                                        ))
                                )}
                            </div>
                        </>
                    )}
            </summary>
            <div className="calls-filter-menu">
                <p className="px-2 py-1.5 text-xs font-semibold">Filter by status</p>
                <div className="my-1 h-px bg-border" />
                {options.map((option) => {
                    const isSelected = selectedValues.has(option.value)
                    return (
                        <label
                            key={option.value}
                            className="calls-filter-option"
                        >
                            <input
                                type="checkbox"
                                checked={isSelected}
                                className="size-3.5 accent-primary"
                                onChange={(event) => {
                                const next = new Set(selectedValues)
                                if (event.target.checked) {
                                    next.add(option.value)
                                } else {
                                    next.delete(option.value)
                                }
                                onSelectedChange(next)
                            }}
                            />
                            {option.label}
                        </label>
                    )
                })}
                {selectedValues.size > 0 && (
                    <div className="mt-1 border-t border-border pt-1">
                        <button
                            type="button"
                            className="calls-filter-clear"
                            onClick={() => onSelectedChange(new Set())}
                        >
                            Clear filters
                        </button>
                    </div>
                )}
            </div>
        </details>
    )
}

// ── Call Detail Dialog ─────────────────────────────────────────────────────────

interface CallDetailProps {
    callId: string | null
    statuses: WorkflowStatus[]
    onClose: () => void
    onResolved: (callId: string) => void
}

function CallDetailDialog({ callId, statuses, onClose, onResolved }: CallDetailProps) {
    const [detail, setDetail] = useState<CallDetail | null>(null)
    const [loading, setLoading] = useState(false)
    const [resolving, setResolving] = useState(false)
    const [savingStatus, setSavingStatus] = useState(false)
    const [note, setNote] = useState("")

    useEffect(() => {
        if (!callId) { setDetail(null); setNote(""); return }
        setLoading(true)
        getCall(callId)
            .then(setDetail)
            .catch((e) => toast.error(e instanceof Error ? e.message : "Failed to load call"))
            .finally(() => setLoading(false))
    }, [callId])

    async function handleAssignStatus(statusId: string | null) {
        if (!callId) return
        setSavingStatus(true)
        try {
            await assignCallStatus(callId, statusId)
            const fresh = await getCall(callId)
            setDetail(fresh)
            onResolved(callId)
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to update status")
        } finally {
            setSavingStatus(false)
        }
    }

    async function handleResolve() {
        if (!callId) return
        setResolving(true)
        try {
            await resolveCallback(callId, note || undefined)
            toast.success("Callback marked as resolved")
            onResolved(callId)
            onClose()
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to resolve")
        } finally {
            setResolving(false)
        }
    }

    const isNeedsCallback = detail?.call_tags?.includes("needs_callback")
    const alreadyResolved = detail?.callback_resolved ?? false

    return (
        <Dialog open={!!callId} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="calls-detail-dialog max-w-2xl">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        {detail?.call_direction === "inbound" ? (
                            <PhoneIncoming className="h-4 w-4 text-blue-500" />
                        ) : (
                            <PhoneOutgoing className="h-4 w-4 text-purple-500" />
                        )}
                        Call Detail
                    </DialogTitle>
                </DialogHeader>

                {loading ? (
                    <div className="space-y-3 py-2">
                        <Skeleton className="h-5 w-48" />
                        <Skeleton className="h-4 w-full" />
                        <Skeleton className="h-4 w-3/4" />
                        <Skeleton className="h-32 w-full" />
                    </div>
                ) : detail ? (
                    <div className="space-y-4 text-sm">
                        {/* Contact */}
                        <div>
                            <div className="font-medium text-base">
                                {detail.contact?.full_name ?? (
                                    <span className="text-muted-foreground">Unknown caller</span>
                                )}
                                {detail.is_new_patient && (
                                    <span className="ml-2 inline-flex items-center gap-1 text-xs text-indigo-600 font-normal">
                                        <UserPlus className="h-3.5 w-3.5" /> New Patient
                                    </span>
                                )}
                            </div>
                            {detail.phone_reveal_available && (
                                <RevealablePhone
                                    callId={detail.id}
                                    masked={detail.phone_masked}
                                    available={detail.phone_reveal_available}
                                    className="mt-1 text-sm"
                                />
                            )}
                        </div>

                        {/* Tags */}
                        <div className="flex flex-wrap gap-1.5">
                            {detail.call_tags.length > 0
                                ? detail.call_tags.map((t) => <TagBadge key={t} tag={t} />)
                                : <span className="text-xs text-muted-foreground">No tags</span>
                            }
                            <SentimentBadge sentiment={detail.patient_sentiment} />
                        </div>

                        {/* Workflow status (human-assigned) */}
                        {statuses.length > 0 && (
                            <div>
                                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">Status</p>
                                <div className="flex items-center gap-2">
                                    <div className="w-48">
                                        <StatusSelect
                                            statuses={statuses}
                                            value={detail.workflow_status?.id ?? null}
                                            onChange={handleAssignStatus}
                                            saving={savingStatus}
                                        />
                                    </div>
                                    {detail.workflow_status && <StatusBadge status={detail.workflow_status} />}
                                </div>
                            </div>
                        )}

                        {/* Date & duration */}
                        <div className="grid grid-cols-2 gap-2 rounded-lg border bg-muted p-3 text-xs">
                            <div>
                                <p className="text-muted-foreground">Date & Time</p>
                                <p className="font-medium mt-0.5">{formatDateTime(detail.call_date, detail.call_time)}</p>
                            </div>
                            <div>
                                <p className="text-muted-foreground">Duration</p>
                                <p className="font-medium mt-0.5">{formatDuration(detail.call_duration_seconds)}</p>
                            </div>
                        </div>

                        {/* Summary */}
                        {detail.summary && (
                            <div>
                                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">AI Summary</p>
                                <p className="text-sm leading-relaxed rounded-lg border bg-muted p-3">{detail.summary}</p>
                            </div>
                        )}

                        {/* Appointment detail */}
                        {detail.next_action && (
                            <div>
                                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">Appointment Detail</p>
                                <p className="text-sm leading-relaxed rounded-lg border bg-muted p-3">{detail.next_action}</p>
                            </div>
                        )}

                        {/* Recording */}
                        <RecordingSection detail={detail} />

                        {/* Custom fields */}
                        <CustomFieldsSection callId={detail.id} fields={detail.custom_fields} />

                        {/* Transcript — 3-tab viewer */}
                        <TranscriptSection detail={detail} />

                        {/* Callback resolution */}
                        {isNeedsCallback && !alreadyResolved && (
                            <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 p-3 space-y-2">
                                <p className="text-xs font-medium text-amber-600 dark:text-amber-400">This call needs a callback</p>
                                <Input
                                    placeholder="Resolution note (optional)…"
                                    value={note}
                                    onChange={(e) => setNote(e.target.value)}
                                    className="text-sm"
                                />
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
                        )}
                        {isNeedsCallback && alreadyResolved && (
                            <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-600 dark:text-emerald-400">
                                <CheckCircle2 className="h-4 w-4 shrink-0" />
                                Callback resolved
                            </div>
                        )}
                    </div>
                ) : null}
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
                    <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                            <Skeleton className="h-8 w-8 rounded-full" />
                            <div className="space-y-1.5">
                                <Skeleton className="h-4 w-24" />
                                <Skeleton className="h-3 w-16" />
                            </div>
                        </div>
                    </td>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-16" /></td>
                    <td className="px-4 py-3"><div className="flex gap-1"><Skeleton className="h-5 w-20 rounded-full" /><Skeleton className="h-5 w-16 rounded-full" /></div></td>
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
    const { lastEvent } = useSSE()
    const [searchParams, setSearchParams] = useSearchParams()
    const [data, setData] = useState<CallsListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [selectedCallId, setSelectedCallId] = useState<string | null>(
        searchParams.get("detail")
    )
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

    // Clear the query param once consumed
    useEffect(() => {
        if (searchParams.has("detail")) {
            setSearchParams((prev) => { prev.delete("detail"); return prev }, { replace: true })
        }
    }, []) // eslint-disable-line react-hooks/exhaustive-deps

    // Filters
    const [search, setSearch] = useState("")
    const [selectedTags, setSelectedTags] = useState<string[]>([])
    const [selectedStatusIds, setSelectedStatusIds] = useState<string[]>([])
    const [directionFilter, setDirectionFilter] = useState("")
    const [dateFrom, setDateFrom] = useState("")
    const [dateTo, setDateTo] = useState("")

    // Tenant-defined workflow statuses (for the filter + assign control).
    const [statuses, setStatuses] = useState<WorkflowStatus[]>([])
    useEffect(() => {
        listWorkflowStatuses().then(setStatuses).catch(() => { /* non-fatal */ })
    }, [])

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
    useEffect(() => { setPage(0) }, [debouncedSearch, selectedTags, selectedStatusIds, directionFilter, dateFrom, dateTo])

    const fetchCalls = useCallback(async () => {
        setLoading(true)
        try {
            const result = await listCalls({
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
                tags: selectedTags.length ? selectedTags : undefined,
                status_ids: selectedStatusIds.length ? selectedStatusIds : undefined,
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
    }, [page, selectedTags, selectedStatusIds, directionFilter, debouncedSearch, dateFrom, dateTo])

    useEffect(() => { fetchCalls() }, [fetchCalls])

    useEffect(() => {
        if (lastEvent?.type !== "calls_updated") {
            return
        }
        fetchCalls()
    }, [fetchCalls, lastEvent])

    const hasFilters = !!(selectedTags.length || directionFilter || dateFrom || dateTo || search)

    function clearFilters() {
        setSearch("")
        setSelectedTags([])
        setDirectionFilter("")
        setDateFrom("")
        setDateTo("")
    }

    const total = data?.total ?? 0
    const pageCount = Math.ceil(total / PAGE_SIZE)
    const from = total === 0 ? 0 : page * PAGE_SIZE + 1
    const to = Math.min((page + 1) * PAGE_SIZE, total)

    return (
        <div className="ui-page ui-page-stack">
            <PageHeader
                icon={Phone}
                art={callsArt}
                title={
                    <>
                        Calls
                        {!loading && data && (
                            <span className="page-header-count">({total.toLocaleString()})</span>
                        )}
                    </>
                }
                description="Browse and review all patient calls handled by your voice agent."
                actions={
                    <Button variant="outline" size="sm" onClick={fetchCalls} disabled={loading} className="gap-1.5">
                        <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                }
            />

            {/* Filters */}
            <div className="calls-toolbar">
                <div className="calls-toolbar-controls">
                    <div className="calls-search">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                        <Input
                            placeholder="Search by patient name..."
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            className="w-full pl-8"
                        />
                    </div>
                    <CallsFacetedFilter
                        title="Tags"
                        options={STATUS_OPTIONS}
                        selectedValues={new Set(selectedTags)}
                        onSelectedChange={(s) => setSelectedTags(Array.from(s))}
                    />
                    {statuses.length > 0 && (
                        <CallsFacetedFilter
                            title="Status"
                            options={statuses.map((s) => ({ value: s.id, label: s.name }))}
                            selectedValues={new Set(selectedStatusIds)}
                            onSelectedChange={(s) => setSelectedStatusIds(Array.from(s))}
                        />
                    )}
                    <div className="calls-direction-filter">
                        <UiSelect
                            aria-label="Direction"
                            value={directionFilter || "all"}
                            onChange={(event) => setDirectionFilter(event.target.value === "all" ? "" : event.target.value)}
                        >
                            <option value="all">Direction</option>
                            {DIRECTION_OPTIONS.map((o) => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                        </UiSelect>
                    </div>
                    <Separator orientation="vertical" className="mx-1 h-6 hidden sm:block" />
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
                            className="calls-reset hidden text-muted-foreground sm:flex"
                        >
                            Reset
                            <X className="ml-2 h-4 w-4" />
                        </Button>
                    )}
                </div>
                {/* Mobile Clear Button */}
                {hasFilters && (
                    <Button
                        variant="ghost"
                        onClick={clearFilters}
                        className="calls-reset ml-2 text-muted-foreground sm:hidden"
                    >
                        <X className="h-4 w-4" />
                    </Button>
                )}
                <ViewSwitch value={viewMode} onChange={changeView} />
            </div>

            {/* Conversation (inbox) view */}
            {viewMode === "conversation" ? (
                <ConversationView
                    items={(data?.items ?? []).map((c) => ({
                        id: c.id,
                        name: c.contact?.full_name ?? null,
                        date: c.call_date,
                        time: c.call_time,
                        summary: c.summary,
                        direction: c.call_direction,
                        tags: c.call_tags,
                        isNewPatient: c.is_new_patient,
                        needsCallback: c.call_tags.includes("needs_callback") && !c.callback_resolved,
                        status: c.workflow_status,
                    }))}
                    loading={loading}
                    total={total}
                    page={page}
                    pageCount={pageCount}
                    from={from}
                    to={to}
                    hasFilters={hasFilters}
                    onPageChange={setPage}
                    onResolved={fetchCalls}
                    statuses={statuses}
                    emptyTitle="No calls found"
                    emptyHint="Calls will appear here once your voice agent starts taking calls."
                />
            ) : (
            /* Table */
            <Card>
                <CardContent className="p-0">
                    <div className="calls-table-scroll">
                        <Table className="calls-table">
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Patient</TableHead>
                                    <TableHead>Direction</TableHead>
                                    <TableHead>Tags</TableHead>
                                    <TableHead>Sentiment</TableHead>
                                    <TableHead className="whitespace-nowrap">Duration</TableHead>
                                    <TableHead>Summary</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {loading ? (
                                    <SkeletonRows />
                                ) : !data || data.items.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={6} className="px-4 py-16 text-center">
                                            <div className="flex flex-col items-center gap-3 text-muted-foreground">
                                                <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center">
                                                    <Phone className="h-6 w-6 opacity-40" />
                                                </div>
                                                <div>
                                                    <p className="font-medium text-sm text-foreground/70">No calls found</p>
                                                    <p className="text-xs mt-0.5">
                                                        {hasFilters
                                                            ? "Try adjusting or clearing your filters."
                                                            : "Calls will appear here once your voice agent starts taking calls."}
                                                    </p>
                                                </div>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    data.items.map((call) => (
                                        <CallRow
                                            key={call.id}
                                            call={call}
                                            onClick={() => setSelectedCallId(call.id)}
                                        />
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    </div>

                    {/* Footer: result count (left) + pagination (right) */}
                    {!loading && total > 0 && (
                        <div className="calls-pagination">
                            <p className="text-sm text-muted-foreground">
                                Showing <span className="font-medium text-foreground">{from}–{to}</span> of{" "}
                                <span className="font-medium text-foreground">{total.toLocaleString()}</span> calls
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

            {/* Detail dialog (table view) */}
            <CallDetailDialog
                callId={selectedCallId}
                statuses={statuses}
                onClose={() => setSelectedCallId(null)}
                onResolved={fetchCalls}
            />
        </div>
    )
}

// ── Call row ──────────────────────────────────────────────────────────────────

interface CallRowProps {
    call: CallRecord
    onClick: () => void
}

function CallRow({ call, onClick }: CallRowProps) {
    const name = call.contact?.full_name
    return (
        <TableRow
            className="cursor-pointer hover:bg-muted/50 transition-colors"
            onClick={onClick}
        >
            <TableCell>
                <div className="flex items-center gap-3">
                    {name ? (
                        <div className="grid size-8 shrink-0 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
                            {getInitials(name)}
                        </div>
                    ) : (
                        <div className="grid size-8 shrink-0 place-items-center rounded-full bg-muted text-sm font-semibold text-muted-foreground">
                            ?
                        </div>
                    )}
                    <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                            <span className={name ? "font-medium" : "italic text-muted-foreground"}>
                                {name ?? "Unknown caller"}
                            </span>
                            {call.is_new_patient && (
                                <UserPlus className="h-3.5 w-3.5 text-indigo-500 shrink-0" aria-label="New patient" />
                            )}
                        </div>
                        <p className="whitespace-nowrap text-xs text-muted-foreground">
                            {formatDateTime(call.call_date, call.call_time)}
                        </p>
                        {call.booked_appointment_type_name && (
                            <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-2xs font-medium text-emerald-600 dark:text-emerald-400">
                                Booked: {call.booked_appointment_type_name}
                            </span>
                        )}
                    </div>
                </div>
            </TableCell>

            <TableCell>
                {call.call_direction === "inbound" ? (
                    <span className="inline-flex items-center gap-1 text-xs text-blue-600 dark:text-blue-400 font-medium bg-blue-500/10 px-2 py-0.5 rounded-full">
                        <PhoneIncoming className="h-3.5 w-3.5" /> Inbound
                    </span>
                ) : call.call_direction === "outbound" ? (
                    <span className="inline-flex items-center gap-1 text-xs text-purple-600 font-medium bg-purple-500/10 px-2 py-0.5 rounded-full">
                        <PhoneOutgoing className="h-3.5 w-3.5" /> Outbound
                    </span>
                ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                )}
            </TableCell>

            <TableCell>
                <div className="flex flex-wrap items-center gap-1">
                    {call.workflow_status && <StatusBadge status={call.workflow_status} />}
                    {call.call_tags.length > 0
                        ? call.call_tags.slice(0, 3).map((t) => <TagBadge key={t} tag={t} />)
                        : (!call.workflow_status && <span className="text-xs text-muted-foreground">—</span>)
                    }
                    {call.call_tags.length > 3 && (
                        <Badge variant="secondary" className="text-2xs">+{call.call_tags.length - 3}</Badge>
                    )}
                </div>
            </TableCell>

            <TableCell><SentimentBadge sentiment={call.patient_sentiment} /></TableCell>

            <TableCell className="text-muted-foreground tabular-nums whitespace-nowrap">
                {formatDuration(call.call_duration_seconds)}
            </TableCell>

            <TableCell className="max-w-[280px]">
                {call.summary ? (
                    <p className="text-muted-foreground line-clamp-2 leading-relaxed">{call.summary}</p>
                ) : (
                    <span className="text-muted-foreground">—</span>
                )}
            </TableCell>
        </TableRow>
    )
}
