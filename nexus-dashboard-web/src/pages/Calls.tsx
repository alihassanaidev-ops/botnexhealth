import { useState, useCallback, useEffect, useRef } from "react"
import { useSearchParams } from "react-router-dom"
import { MaskedPHI } from "@/components/ui/masked-phi"
import {
    Phone,
    PhoneIncoming,
    PhoneOutgoing,
    CalendarIcon,
    Search,
    ChevronLeft,
    ChevronRight,
    X,
    UserPlus,
    CheckCircle2,
    RefreshCcw,
    PlusCircle,
} from "lucide-react"
import { format } from "date-fns"
import type { DateRange } from "react-day-picker"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
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
    DropdownMenu,
    DropdownMenuCheckboxItem,
    DropdownMenuContent,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
import { listCalls, getCall, resolveCallback } from "@/lib/calls-api"
import { STATUS_OPTIONS, DIRECTION_OPTIONS } from "@/lib/constants"
import { cn } from "@/lib/utils"
import type { CallRecord, CallDetail, CallsListResponse, CustomFieldValue, TranscriptTurn } from "@/types"

// ── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 25
const POLL_INTERVAL_MS = 30_000

const STATUS_MAP = Object.fromEntries(STATUS_OPTIONS.map((o) => [o.value, o]))

// ── Style helpers ─────────────────────────────────────────────────────────────

function TagBadge({ tag }: { tag: string }) {
    const opt = STATUS_MAP[tag]
    const cls = opt?.color ?? "bg-zinc-500/15 text-zinc-500 border-zinc-500/25"
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
        Positive: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
        Negative: "bg-red-500/15 text-red-600 dark:text-red-400",
        Neutral: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400",
    }
    const cls = map[sentiment] ?? "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400"
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
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="h-8">
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
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="w-[200px]" align="start">
                <DropdownMenuLabel>Filter by status</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {options.map((option) => {
                    const isSelected = selectedValues.has(option.value)
                    return (
                        <DropdownMenuCheckboxItem
                            key={option.value}
                            checked={isSelected}
                            onCheckedChange={(checked) => {
                                const next = new Set(selectedValues)
                                if (checked) {
                                    next.add(option.value)
                                } else {
                                    next.delete(option.value)
                                }
                                onSelectedChange(next)
                            }}
                        >
                            {option.label}
                        </DropdownMenuCheckboxItem>
                    )
                })}
                {selectedValues.size > 0 && (
                    <>
                        <DropdownMenuSeparator />
                        <DropdownMenuCheckboxItem
                            className="justify-center text-center font-medium"
                            onCheckedChange={() => onSelectedChange(new Set())}
                            checked={false}
                        >
                            Clear filters
                        </DropdownMenuCheckboxItem>
                    </>
                )}
            </DropdownMenuContent>
        </DropdownMenu>
    )
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

interface CallsDateRangeFilterProps {
    from: string
    to: string
    onChange: (next: { from: string; to: string }) => void
}

function CallsDateRangeFilter({ from, to, onChange }: CallsDateRangeFilterProps) {
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
            <div className="grid grid-cols-2 gap-2 rounded-lg border bg-muted p-3 text-xs">
                {fields.map((f) => (
                    <div key={f.field_key}>
                        <p className="text-muted-foreground flex items-center gap-1">
                            {f.field_name}
                        </p>
                        <MaskedPHI
                            value={renderFieldValue(f)}
                            isPhi={f.is_phi}
                            className="font-medium mt-0.5"
                        />
                    </div>
                ))}
            </div>
        </div>
    )
}

// ── Transcript Chat UI ──────────────────────────────────────────────────────

function TranscriptChatBubbles({ turns }: { turns: TranscriptTurn[] }) {
    if (turns.length === 0) {
        return <p className="text-xs text-muted-foreground italic p-3">No transcript turns available.</p>
    }
    return (
        <div className="space-y-2 p-3">
            {turns.map((turn, i) => {
                if (turn.role === "tool_call_invocation") {
                    return (
                        <div key={i} className="flex justify-center">
                            <span className="inline-flex items-center gap-1 rounded-full bg-muted border px-2.5 py-0.5 text-[10px] text-muted-foreground">
                                ⚙ Agent triggered: <span className="font-medium">{turn.name ?? "action"}</span>
                            </span>
                        </div>
                    )
                }
                if (turn.role === "tool_call_result") return null
                if (!turn.content) return null

                const isAgent = turn.role === "agent"
                return (
                    <div key={i} className={`flex ${isAgent ? "justify-start" : "justify-end"}`}>
                        <div
                            className={`max-w-[80%] rounded-2xl px-3 py-2 text-xs leading-relaxed shadow-sm ${isAgent
                                ? "bg-background border text-foreground rounded-tl-sm"
                                : "bg-primary text-primary-foreground rounded-tr-sm"
                                }`}
                        >
                            <p className={`font-semibold mb-0.5 text-[10px] ${isAgent ? "opacity-50" : "opacity-75"
                                }`}>
                                {isAgent ? "AI Assistant" : "Caller"}
                            </p>
                            {turn.content}
                        </div>
                    </div>
                )
            })}
        </div>
    )
}

type TranscriptTab = "scrubbed" | "full" | "raw"

function TranscriptSection({ detail }: { detail: CallDetail }) {
    const [tab, setTab] = useState<TranscriptTab>("scrubbed")

    const hasScrubbed = !!detail.scrubbed_transcript_with_tool_calls?.length
    const hasFull = !!detail.transcript_with_tool_calls?.length
    const hasRaw = !!detail.transcript

    if (!hasScrubbed && !hasFull && !hasRaw) return null

    const tabs: { id: TranscriptTab; label: string; available: boolean }[] = [
        { id: "scrubbed", label: "Scrubbed", available: hasScrubbed },
        { id: "full", label: "Full", available: hasFull },
        { id: "raw", label: "Raw Text", available: hasRaw },
    ]

    // Auto-select best available tab
    const activeTab = tabs.find(t => t.id === tab && t.available)
        ? tab
        : (tabs.find(t => t.available)?.id ?? "scrubbed")

    return (
        <div>
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1.5">Transcript</p>

            {/* Tab switcher */}
            <div className="flex gap-1 mb-2">
                {tabs.map(t => t.available && (
                    <button
                        key={t.id}
                        type="button"
                        onClick={() => setTab(t.id)}
                        className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${activeTab === t.id
                            ? "bg-primary text-primary-foreground"
                            : "bg-muted text-muted-foreground hover:bg-muted/70"
                            }`}
                    >
                        {t.label}
                        {t.id === "scrubbed" && (
                            <span className="ml-1.5 text-[9px] opacity-60 font-normal">HIPAA ✓</span>
                        )}
                    </button>
                ))}
            </div>

            {/* Content */}
            <div className="rounded-lg border bg-muted max-h-64 overflow-y-auto">
                {activeTab === "scrubbed" && hasScrubbed && (
                    <TranscriptChatBubbles turns={detail.scrubbed_transcript_with_tool_calls!} />
                )}
                {activeTab === "full" && hasFull && (
                    <TranscriptChatBubbles turns={detail.transcript_with_tool_calls!} />
                )}
                {activeTab === "raw" && hasRaw && (
                    <pre className="text-xs leading-relaxed p-3 whitespace-pre-wrap font-sans">
                        {detail.transcript}
                    </pre>
                )}
            </div>
        </div>
    )
}

// ── Call Detail Dialog ─────────────────────────────────────────────────────────

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
                        {detail.recording_url && (
                            <div>
                                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">Call Recording</p>
                                <div className="rounded-lg border bg-muted p-3 flex items-center justify-center">
                                    <audio controls className="w-full h-10 outline-none" src={detail.recording_url}>
                                        Your browser does not support the audio element.
                                    </audio>
                                </div>
                            </div>
                        )}

                        {/* Custom fields */}
                        <CustomFieldsSection fields={detail.custom_fields} />

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
    const [searchParams, setSearchParams] = useSearchParams()
    const [data, setData] = useState<CallsListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [selectedCallId, setSelectedCallId] = useState<string | null>(
        searchParams.get("detail")
    )

    // Clear the query param once consumed
    useEffect(() => {
        if (searchParams.has("detail")) {
            setSearchParams((prev) => { prev.delete("detail"); return prev }, { replace: true })
        }
    }, []) // eslint-disable-line react-hooks/exhaustive-deps

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
        <div className="flex-1 space-y-6 bg-gradient-to-b from-background via-background to-accent/20 p-8 pt-6">
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
            <div className="flex items-center justify-between">
                <div className="flex flex-1 items-center space-x-2 overflow-x-auto pb-2 -mb-2">
                    <div className="relative">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                        <Input
                            placeholder="Search by patient name..."
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            className="h-8 pl-8 w-[150px] lg:w-[250px]"
                        />
                    </div>
                    <CallsFacetedFilter
                        title="Status"
                        options={STATUS_OPTIONS}
                        selectedValues={new Set(selectedTags)}
                        onSelectedChange={(s) => setSelectedTags(Array.from(s))}
                    />
                    <Select value={directionFilter || "all"} onValueChange={(v) => setDirectionFilter(v === "all" ? "" : v)}>
                        <SelectTrigger className="h-8 w-[150px]">
                            <SelectValue placeholder="Direction" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">Direction</SelectItem>
                            {DIRECTION_OPTIONS.map((o) => (
                                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Separator orientation="vertical" className="mx-1 h-6 hidden sm:block" />
                    <CallsDateRangeFilter
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
                            className="h-8 px-2 lg:px-3 text-muted-foreground hidden sm:flex"
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
                        className="h-8 px-2 sm:hidden text-muted-foreground ml-2"
                    >
                        <X className="h-4 w-4" />
                    </Button>
                )}
            </div>

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
                        <Table className="w-full text-sm">
                            <TableHeader className="border-b border-border bg-muted">
                                <TableRow>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Date & Time</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Patient</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Direction</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Tags</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Sentiment</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap">Duration</TableHead>
                                    <TableHead className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">Summary</TableHead>
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
        <TableRow
            className="cursor-pointer hover:bg-muted/50 transition-colors"
            onClick={onClick}
        >
            <TableCell className="whitespace-nowrap text-muted-foreground">
                {formatDateTime(call.call_date, call.call_time)}
            </TableCell>

            <TableCell>
                <div className="flex items-center gap-1.5">
                    <span className={call.contact?.full_name ? "font-medium" : "text-muted-foreground"}>
                        {call.contact?.full_name ?? "Unknown"}
                    </span>
                    {call.is_new_patient && (
                        <UserPlus className="h-3.5 w-3.5 text-indigo-500 shrink-0" aria-label="New patient" />
                    )}
                </div>
            </TableCell>

            <TableCell>
                {call.call_direction === "inbound" ? (
                    <span className="inline-flex items-center gap-1 text-xs text-blue-600 font-medium bg-blue-500/10 px-2 py-0.5 rounded-full">
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
                <div className="flex flex-wrap gap-1">
                    {call.call_tags.length > 0
                        ? call.call_tags.slice(0, 3).map((t) => <TagBadge key={t} tag={t} />)
                        : <span className="text-xs text-muted-foreground">—</span>
                    }
                    {call.call_tags.length > 3 && (
                        <Badge variant="secondary" className="text-[10px]">+{call.call_tags.length - 3}</Badge>
                    )}
                </div>
            </TableCell>

            <TableCell>{sentimentBadge(call.patient_sentiment)}</TableCell>

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
