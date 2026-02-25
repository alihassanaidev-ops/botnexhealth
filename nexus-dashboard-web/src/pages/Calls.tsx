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
    CheckCircle2,
    RefreshCcw,
    Shield,
} from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
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
import { listCalls, getCall, resolveCallback } from "@/lib/calls-api"
import type { CallRecord, CallDetail, CallsListResponse, CustomFieldValue } from "@/types"

// ── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 25
const POLL_INTERVAL_MS = 30_000

export const STATUS_OPTIONS: { value: string; label: string; color: string }[] = [
    { value: "appointment_booked", label: "Appointment Booked", color: "bg-green-100 text-green-800 border-green-200" },
    { value: "appointment_rescheduled", label: "Rescheduled", color: "bg-blue-100 text-blue-800 border-blue-200" },
    { value: "appointment_cancelled", label: "Cancelled", color: "bg-zinc-100 text-zinc-600 border-zinc-200" },
    { value: "emergency", label: "Emergency", color: "bg-red-100 text-red-800 border-red-200" },
    { value: "complaint", label: "Complaint", color: "bg-orange-100 text-orange-800 border-orange-200" },
    { value: "needs_callback", label: "Needs Callback", color: "bg-amber-100 text-amber-800 border-amber-200" },
    { value: "faq_handled", label: "FAQ Handled", color: "bg-sky-100 text-sky-800 border-sky-200" },
    { value: "financial_inquiry", label: "Financial Inquiry", color: "bg-violet-100 text-violet-800 border-violet-200" },
    { value: "transferred", label: "Transferred", color: "bg-teal-100 text-teal-800 border-teal-200" },
    { value: "insurance_verified", label: "Insurance Verified", color: "bg-emerald-100 text-emerald-800 border-emerald-200" },
    { value: "insurance_unverified", label: "Insurance Unverified", color: "bg-rose-100 text-rose-800 border-rose-200" },
    { value: "no_action_needed", label: "No Action Needed", color: "bg-zinc-100 text-zinc-500 border-zinc-200" },
]

const STATUS_MAP = Object.fromEntries(STATUS_OPTIONS.map((o) => [o.value, o]))

const DIRECTION_OPTIONS = [
    { value: "inbound", label: "Inbound" },
    { value: "outbound", label: "Outbound" },
]

// ── Style helpers ─────────────────────────────────────────────────────────────

function TagBadge({ tag }: { tag: string }) {
    const opt = STATUS_MAP[tag]
    const cls = opt?.color ?? "bg-zinc-100 text-zinc-600 border-zinc-200"
    const label = opt?.label ?? tag.replace(/_/g, " ")
    return (
        <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${cls}`}>
            {label}
        </span>
    )
}

function sentimentBadge(sentiment: string | null) {
    if (!sentiment) return <span className="text-xs text-muted-foreground">—</span>
    const map: Record<string, string> = {
        Positive: "bg-green-100 text-green-700",
        Negative: "bg-red-100 text-red-700",
        Neutral: "bg-zinc-100 text-zinc-600",
    }
    const cls = map[sentiment] ?? "bg-zinc-100 text-zinc-600"
    return (
        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
            {sentiment}
        </span>
    )
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

// ── Tag filter toggle ─────────────────────────────────────────────────────────

interface TagToggleProps {
    selected: string[]
    onChange: (tags: string[]) => void
}

function TagFilter({ selected, onChange }: TagToggleProps) {
    function toggle(value: string) {
        onChange(
            selected.includes(value)
                ? selected.filter((t) => t !== value)
                : [...selected, value]
        )
    }

    return (
        <div className="flex flex-wrap gap-1.5">
            {STATUS_OPTIONS.map((opt) => {
                const active = selected.includes(opt.value)
                return (
                    <button
                        key={opt.value}
                        type="button"
                        onClick={() => toggle(opt.value)}
                        className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium transition-all
                            ${active
                                ? `${opt.color} ring-2 ring-offset-1 ring-current`
                                : "border-border bg-background text-muted-foreground hover:border-foreground/30 hover:text-foreground"
                            }`}
                    >
                        {opt.label}
                    </button>
                )
            })}
        </div>
    )
}

// ── Custom Fields Section ─────────────────────────────────────────────────

function renderFieldValue(field: CustomFieldValue): string {
    if (field.value === null || field.value === undefined) return "—"
    switch (field.field_type) {
        case "boolean":
            return field.value.toLowerCase() === "true" ? "Yes" : "No"
        case "number":
            return field.value
        case "date": {
            try {
                const d = new Date(field.value)
                return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
            } catch {
                return field.value
            }
        }
        default:
            return field.value
    }
}

function CustomFieldsSection({ fields }: { fields: CustomFieldValue[] }) {
    if (!fields || fields.length === 0) return null
    return (
        <div>
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">
                Additional Details
            </p>
            <div className="grid grid-cols-2 gap-2 rounded-lg border bg-muted/30 p-3 text-xs">
                {fields.map((f) => (
                    <div key={f.field_key}>
                        <p className="text-muted-foreground flex items-center gap-1">
                            {f.field_name}
                            {f.is_phi && <Shield className="h-3 w-3 text-amber-500" />}
                        </p>
                        <p className="font-medium mt-0.5">{renderFieldValue(f)}</p>
                    </div>
                ))}
            </div>
        </div>
    )
}

// ── Call Detail Dialog ────────────────────────────────────────────────────────

interface CallDetailProps {
    callId: string | null
    onClose: () => void
    onResolved: (callId: string) => void
}

function CallDetailDialog({ callId, onClose, onResolved }: CallDetailProps) {
    const [detail, setDetail] = useState<CallDetail | null>(null)
    const [loading, setLoading] = useState(false)
    const [resolving, setResolving] = useState(false)
    const [note, setNote] = useState("")

    useEffect(() => {
        if (!callId) { setDetail(null); setNote(""); return }
        setLoading(true)
        getCall(callId)
            .then(setDetail)
            .catch((e) => toast.error(e instanceof Error ? e.message : "Failed to load call"))
            .finally(() => setLoading(false))
    }, [callId])

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
            <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
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

                        {/* Tags */}
                        <div className="flex flex-wrap gap-1.5">
                            {detail.call_tags.length > 0
                                ? detail.call_tags.map((t) => <TagBadge key={t} tag={t} />)
                                : <span className="text-xs text-muted-foreground">No tags</span>
                            }
                            {sentimentBadge(detail.patient_sentiment)}
                        </div>

                        {/* Date & duration */}
                        <div className="grid grid-cols-2 gap-2 rounded-lg border bg-muted/40 p-3 text-xs">
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
                                <p className="text-sm leading-relaxed rounded-lg border bg-muted/30 p-3">{detail.summary}</p>
                            </div>
                        )}

                        {/* Appointment detail */}
                        {detail.next_action && (
                            <div>
                                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">Appointment Detail</p>
                                <p className="text-sm leading-relaxed rounded-lg border bg-muted/30 p-3">{detail.next_action}</p>
                            </div>
                        )}

                        {/* Recording */}
                        {detail.recording_url && (
                            <div>
                                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">Call Recording</p>
                                <div className="rounded-lg border bg-muted/30 p-3 flex items-center justify-center">
                                    <audio controls className="w-full h-10 outline-none" src={detail.recording_url}>
                                        Your browser does not support the audio element.
                                    </audio>
                                </div>
                            </div>
                        )}

                        {/* Custom fields */}
                        <CustomFieldsSection fields={detail.custom_fields} />

                        {/* Transcript */}
                        {detail.transcript && (
                            <div>
                                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">Transcript</p>
                                <pre className="text-xs leading-relaxed rounded-lg border bg-muted/30 p-3 whitespace-pre-wrap font-sans max-h-64 overflow-y-auto">
                                    {detail.transcript}
                                </pre>
                            </div>
                        )}

                        {/* Callback resolution */}
                        {isNeedsCallback && !alreadyResolved && (
                            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 space-y-2">
                                <p className="text-xs font-medium text-amber-800">This call needs a callback</p>
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
                            <div className="flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-700">
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
                    <td className="px-4 py-3"><Skeleton className="h-4 w-28" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-4 w-24" /></td>
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
    const [data, setData] = useState<CallsListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [selectedCallId, setSelectedCallId] = useState<string | null>(null)

    // Filters
    const [search, setSearch] = useState("")
    const [selectedTags, setSelectedTags] = useState<string[]>([])
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

    // Reset page on filter change
    useEffect(() => { setPage(0) }, [debouncedSearch, selectedTags, directionFilter, dateFrom, dateTo])

    const fetchCalls = useCallback(async () => {
        setLoading(true)
        try {
            const result = await listCalls({
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
                tags: selectedTags.length ? selectedTags : undefined,
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
    }, [page, selectedTags, directionFilter, debouncedSearch, dateFrom, dateTo])

    useEffect(() => { fetchCalls() }, [fetchCalls])

    // 30-second auto-poll
    useEffect(() => {
        const id = setInterval(fetchCalls, POLL_INTERVAL_MS)
        return () => clearInterval(id)
    }, [fetchCalls])

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
                <div className="flex items-center gap-3">
                    {!loading && data && (
                        <div className="text-right">
                            <p className="text-2xl font-bold tabular-nums">{total.toLocaleString()}</p>
                            <p className="text-xs text-muted-foreground">total calls</p>
                        </div>
                    )}
                    <Button variant="outline" size="sm" onClick={fetchCalls} disabled={loading} className="gap-1.5">
                        <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                </div>
            </div>

            {/* Filters */}
            <Card>
                <CardContent className="pt-4 pb-4 space-y-3">
                    {/* Row 1: search + direction + dates */}
                    <div className="flex flex-wrap gap-3 items-end">
                        <div className="relative flex-1 min-w-[180px] max-w-xs">
                            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" />
                            <Input
                                placeholder="Search by patient name…"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                className="pl-9"
                            />
                        </div>

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

                        <div className="flex flex-col gap-1">
                            <span className="text-xs text-muted-foreground pl-0.5">From</span>
                            <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-[145px]" />
                        </div>
                        <div className="flex flex-col gap-1">
                            <span className="text-xs text-muted-foreground pl-0.5">To</span>
                            <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-[145px]" />
                        </div>

                        {hasFilters && (
                            <Button variant="ghost" size="sm" onClick={clearFilters} className="gap-1.5 self-end">
                                <X className="h-3.5 w-3.5" /> Clear
                            </Button>
                        )}
                    </div>

                    {/* Row 2: tag toggles */}
                    <div>
                        <p className="text-xs text-muted-foreground mb-1.5">Filter by tag (select one or more):</p>
                        <TagFilter selected={selectedTags} onChange={setSelectedTags} />
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
                                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">Tags</th>
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
                                            onClick={() => setSelectedCallId(call.id)}
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
                        <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)} className="gap-1">
                            <ChevronLeft className="h-4 w-4" /> Previous
                        </Button>
                        <Button variant="outline" size="sm" disabled={page >= pageCount - 1} onClick={() => setPage((p) => p + 1)} className="gap-1">
                            Next <ChevronRight className="h-4 w-4" />
                        </Button>
                    </div>
                </div>
            )}

            {/* Detail dialog */}
            <CallDetailDialog
                callId={selectedCallId}
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
    return (
        <tr
            className="border-b border-border/50 hover:bg-muted/30 cursor-pointer transition-colors"
            onClick={onClick}
        >
            <td className="px-4 py-3 whitespace-nowrap text-xs text-muted-foreground">
                {formatDateTime(call.call_date, call.call_time)}
            </td>

            <td className="px-4 py-3">
                <div className="flex items-center gap-1.5">
                    <span className={call.contact?.full_name ? "font-medium" : "text-muted-foreground"}>
                        {call.contact?.full_name ?? "Unknown"}
                    </span>
                    {call.is_new_patient && (
                        <UserPlus className="h-3.5 w-3.5 text-indigo-500 shrink-0" aria-label="New patient" />
                    )}
                </div>
            </td>

            <td className="px-4 py-3">
                {call.call_direction === "inbound" ? (
                    <span className="inline-flex items-center gap-1 text-xs text-blue-600 font-medium">
                        <PhoneIncoming className="h-3.5 w-3.5" /> Inbound
                    </span>
                ) : call.call_direction === "outbound" ? (
                    <span className="inline-flex items-center gap-1 text-xs text-purple-600 font-medium">
                        <PhoneOutgoing className="h-3.5 w-3.5" /> Outbound
                    </span>
                ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                )}
            </td>

            {/* Tags — show all, up to 3 visible */}
            <td className="px-4 py-3">
                <div className="flex flex-wrap gap-1">
                    {call.call_tags.length > 0
                        ? call.call_tags.slice(0, 3).map((t) => <TagBadge key={t} tag={t} />)
                        : <span className="text-xs text-muted-foreground">—</span>
                    }
                    {call.call_tags.length > 3 && (
                        <Badge variant="secondary" className="text-[10px]">+{call.call_tags.length - 3}</Badge>
                    )}
                </div>
            </td>

            <td className="px-4 py-3">{sentimentBadge(call.patient_sentiment)}</td>

            <td className="px-4 py-3 text-xs text-muted-foreground tabular-nums whitespace-nowrap">
                {formatDuration(call.call_duration_seconds)}
            </td>

            <td className="px-4 py-3 max-w-[280px]">
                {call.summary ? (
                    <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">{call.summary}</p>
                ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                )}
            </td>
        </tr>
    )
}
