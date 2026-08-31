/**
 * Conversation (inbox) view for Calls — a secondary layout to the table+modal.
 *
 * Three panes: a conversation list rail, a center transcript/recording pane,
 * and a right details/actions pane. It reuses the shared badges, transcript
 * bubbles, and — importantly — the same audit-logged reveal flow as the modal
 * (see ./shared). No PHI is shown until explicitly revealed.
 */

import { NoPmsTriageDetails } from "@/components/calls/shared"
import { CallNotesSection } from "@/components/calls/CallNotes"
import { useCallback, useEffect, useRef, useState } from "react"
import {
    ArrowLeft,
    CheckCircle2,
    ChevronLeft,
    ChevronRight,
    Inbox,
    Loader2,
    MessagesSquare,
    PhoneIncoming,
    PhoneOutgoing,
    UserPlus,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { RevealablePhone } from "@/components/RevealablePhone"
import { toast } from "sonner"
import { useInstitution } from "@/context/InstitutionContext"
import { getCall, resolveCallback } from "@/lib/calls-api"
import { assignCallStatus } from "@/lib/workflow-status-api"
import { cn } from "@/lib/utils"
import type { CallDetail, WorkflowStatus, WorkflowStatusRef } from "@/types"
import {
    CustomFieldsSection,
    RecordingSection,
    TranscriptSection,
    TagBadge,
    SentimentBadge,
    StatusBadge,
    StatusSelect,
} from "./shared"
import { callerLabel, formatDateTime, formatDuration, formatListTimestamp, getInitials } from "./format"

/**
 * Normalized list-rail item. Both the Calls (`CallRecord`) and Callbacks
 * (`CallbackListItem`) pages map their rows into this shape; the center/right
 * panes always load the full `CallDetail` via `getCall(id)`, so the rail only
 * needs enough to render a compact preview.
 */
export interface ConversationSummary {
    /** Call id — used to fetch the full detail. */
    id: string
    name: string | null
    /** Caller's number as the list payload serves it — full for no-PMS location
     *  admins, masked to the last four digits otherwise. Labels the row when
     *  there is no name on the contact. */
    phone?: string | null
    date: string | null
    time: string | null
    summary: string | null
    direction?: string | null
    tags?: string[]
    isNewPatient?: boolean
    /** Shows the amber "Callback" flag in the rail. */
    needsCallback?: boolean
    /** Assigned workflow status (human), shown as a chip in the rail. */
    status?: WorkflowStatusRef | null
}

interface ConversationViewProps {
    items: ConversationSummary[]
    loading: boolean
    total: number
    page: number
    pageCount: number
    from: number
    to: number
    hasFilters: boolean
    onPageChange: (page: number) => void
    /** Called after a callback is resolved so the parent can refetch. */
    onResolved: () => void
    /** The institution's active workflow statuses (for the assign control). */
    statuses?: WorkflowStatus[]
    /** Rail header label. */
    title?: string
    emptyTitle?: string
    emptyHint?: string
}

function DirectionPill({ direction }: { direction: string | null }) {
    if (direction === "inbound") {
        return (
            <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:text-blue-400">
                <PhoneIncoming className="h-3 w-3" /> Inbound
            </span>
        )
    }
    if (direction === "outbound") {
        return (
            <span className="inline-flex items-center gap-1 rounded-full bg-purple-500/10 px-2 py-0.5 text-[11px] font-medium text-purple-600 dark:text-purple-400">
                <PhoneOutgoing className="h-3 w-3" /> Outbound
            </span>
        )
    }
    return null
}

function Avatar({ name, size = "md" }: { name: string | null | undefined; size?: "sm" | "md" }) {
    const dim = size === "sm" ? "size-9 text-[11px]" : "size-10 text-xs"
    if (!name) {
        return (
            <div className={cn("grid shrink-0 place-items-center rounded-full bg-muted font-semibold text-muted-foreground", dim)}>
                ?
            </div>
        )
    }
    return (
        <div className={cn("grid shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-500 to-purple-600 font-semibold text-white", dim)}>
            {getInitials(name)}
        </div>
    )
}

// ── List rail row ─────────────────────────────────────────────────────────────

function ConversationRow({
    item,
    selected,
    onSelect,
}: {
    item: ConversationSummary
    selected: boolean
    onSelect: () => void
}) {
    const name = item.name
    // An unnamed caller is still identified by their number; "Unknown caller"
    // is reserved for calls that carry neither.
    const caller = callerLabel(name, item.phone)
    const tags = item.tags ?? []
    return (
        <button
            type="button"
            onClick={onSelect}
            aria-current={selected}
            className={cn(
                "relative flex w-full items-start gap-3 border-b border-border/60 px-3 py-3 text-left transition-colors",
                "hover:bg-muted/60 focus:outline-none focus-visible:bg-muted/60",
                selected && "bg-muted",
            )}
        >
            {selected && <span className="absolute inset-y-0 left-0 w-0.5 bg-primary" />}
            <Avatar name={name} size="sm" />
            <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                    <span
                        className={cn(
                            "truncate text-sm",
                            caller.kind === "name" && "font-medium",
                            caller.kind === "phone" && "font-medium tabular-nums",
                            caller.kind === "unknown" && "italic text-muted-foreground",
                        )}
                    >
                        {caller.text}
                    </span>
                    <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                        {formatListTimestamp(item.date, item.time)}
                    </span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5">
                    {item.isNewPatient && (
                        <UserPlus className="h-3 w-3 shrink-0 text-indigo-500" aria-label="New patient" />
                    )}
                    <DirectionPill direction={item.direction ?? null} />
                    {item.needsCallback && (
                        <span className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-600 dark:text-amber-400">
                            <span className="h-1.5 w-1.5 rounded-full bg-amber-500" /> Callback
                        </span>
                    )}
                </div>
                {item.summary ? (
                    <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{item.summary}</p>
                ) : (
                    <p className="mt-1 text-xs italic text-muted-foreground/70">No summary</p>
                )}
                {(item.status || tags.length > 0) && (
                    <div className="mt-1.5 flex flex-wrap items-center gap-1">
                        {item.status && <StatusBadge status={item.status} />}
                        {tags.slice(0, 2).map((t) => (
                            <TagBadge key={t} tag={t} />
                        ))}
                        {tags.length > 2 && (
                            <Badge variant="secondary" className="text-[10px]">
                                +{tags.length - 2}
                            </Badge>
                        )}
                    </div>
                )}
            </div>
        </button>
    )
}

function RowSkeletons() {
    return (
        <div>
            {Array.from({ length: 7 }).map((_, i) => (
                <div key={i} className="flex items-start gap-3 border-b border-border/60 px-3 py-3">
                    <Skeleton className="size-9 shrink-0 rounded-full" />
                    <div className="flex-1 space-y-2">
                        <div className="flex justify-between">
                            <Skeleton className="h-3.5 w-24" />
                            <Skeleton className="h-3 w-10" />
                        </div>
                        <Skeleton className="h-3 w-20 rounded-full" />
                        <Skeleton className="h-3 w-full" />
                    </div>
                </div>
            ))}
        </div>
    )
}

// ── Right details / actions pane ────────────────────────────────────────────

function DetailField({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div>
            <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
            {children}
        </div>
    )
}

function CallbackResolver({ detail, onResolved }: { detail: CallDetail; onResolved: () => void }) {
    const [note, setNote] = useState("")
    const [resolving, setResolving] = useState(false)

    useEffect(() => setNote(""), [detail.id])

    const needsCallback = detail.call_tags.includes("needs_callback")
    if (!needsCallback) return null

    if (detail.callback_resolved) {
        return (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                Callback resolved
            </div>
        )
    }

    async function handleResolve() {
        setResolving(true)
        try {
            await resolveCallback(detail.id, note || undefined)
            toast.success("Callback marked as resolved")
            onResolved()
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to resolve")
        } finally {
            setResolving(false)
        }
    }

    return (
        <div className="space-y-2 rounded-lg border border-amber-500/20 bg-amber-500/10 p-3">
            <p className="text-xs font-medium text-amber-600 dark:text-amber-400">This call needs a callback</p>
            <Textarea
                placeholder="Add a resolution note (optional)…"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="min-h-[72px] resize-none bg-background text-sm"
            />
            <Button size="sm" className="w-full gap-1.5" onClick={handleResolve} disabled={resolving}>
                {resolving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                {resolving ? "Resolving…" : "Mark resolved"}
            </Button>
        </div>
    )
}

function StatusField({
    detail,
    statuses,
    onChanged,
}: {
    detail: CallDetail
    statuses: WorkflowStatus[]
    onChanged: () => void
}) {
    const [saving, setSaving] = useState(false)

    async function handleChange(statusId: string | null) {
        setSaving(true)
        try {
            await assignCallStatus(detail.id, statusId)
            onChanged()
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to update status")
        } finally {
            setSaving(false)
        }
    }

    return (
        <DetailField label="Status">
            <StatusSelect
                statuses={statuses}
                value={detail.workflow_status?.id ?? null}
                onChange={handleChange}
                saving={saving}
            />
        </DetailField>
    )
}

function DetailsContent({
    detail,
    statuses,
    onResolved,
}: {
    detail: CallDetail
    statuses: WorkflowStatus[]
    onResolved: () => void
}) {
    const { hasPms, pmsType, isLoading: institutionLoading } = useInstitution()
    const isNoPms = !institutionLoading && (pmsType === "none" || !hasPms)

    return (
            <div className="space-y-4 p-4">
                <div className="grid grid-cols-2 gap-3">
                    <DetailField label="Date & Time">
                        <p className="text-xs font-medium">{formatDateTime(detail.call_date, detail.call_time)}</p>
                    </DetailField>
                    <DetailField label="Duration">
                        <p className="text-xs font-medium tabular-nums">{formatDuration(detail.call_duration_seconds)}</p>
                    </DetailField>
                </div>

                {statuses.length > 0 && (
                    <StatusField detail={detail} statuses={statuses} onChanged={onResolved} />
                )}

                <DetailField label="Sentiment"><SentimentBadge sentiment={detail.patient_sentiment} /></DetailField>

                <DetailField label="Tags">
                    <div className="flex flex-wrap gap-1.5">
                        {detail.call_tags.length > 0 ? (
                            detail.call_tags.map((t) => <TagBadge key={t} tag={t} />)
                        ) : (
                            <span className="text-xs text-muted-foreground">No tags</span>
                        )}
                    </div>
                </DetailField>

                {isNoPms && <NoPmsTriageDetails detail={detail} />}

                {/* Staff notes sit directly under the triage details for every
                    tenant — NexHealth, GoTracker and no-PMS alike. Scoping is
                    enforced server-side by the call's own institution/location. */}
                <CallNotesSection callId={detail.id} />

                {detail.booked_appointment_type_name && (
                    <DetailField label="Booked">
                        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-600 dark:text-emerald-400">
                            {detail.booked_appointment_type_name}
                        </span>
                    </DetailField>
                )}

                {detail.next_action && (
                    <DetailField label="Next Action">
                        <p className="rounded-lg border bg-muted p-2.5 text-xs leading-relaxed">{detail.next_action}</p>
                    </DetailField>
                )}

                <CustomFieldsSection callId={detail.id} fields={detail.custom_fields} />

                <CallbackResolver detail={detail} onResolved={onResolved} />
            </div>
    )
}

/** Right-rail variant (xl+): fills the pane with its own scroll. */
function DetailsPane({
    detail,
    statuses,
    onResolved,
}: {
    detail: CallDetail
    statuses: WorkflowStatus[]
    onResolved: () => void
}) {
    return (
        <ScrollArea className="flex-1">
            <DetailsContent detail={detail} statuses={statuses} onResolved={onResolved} />
        </ScrollArea>
    )
}

// ── Center conversation pane ──────────────────────────────────────────────────

function CenterPane({
    detail,
    statuses,
    loading,
    onBack,
    onResolved,
}: {
    detail: CallDetail | null
    statuses: WorkflowStatus[]
    loading: boolean
    onBack: () => void
    onResolved: () => void
}) {
    if (loading) {
        return (
            <div className="flex flex-1 flex-col">
                <div className="flex items-center gap-3 border-b border-border px-5 py-4">
                    <Skeleton className="size-10 rounded-full" />
                    <div className="space-y-2">
                        <Skeleton className="h-4 w-40" />
                        <Skeleton className="h-3 w-28" />
                    </div>
                </div>
                <div className="flex-1 space-y-3 p-5">
                    <Skeleton className="h-16 w-full" />
                    <Skeleton className="h-10 w-3/4" />
                    <Skeleton className="ml-auto h-10 w-2/3" />
                    <Skeleton className="h-10 w-3/5" />
                </div>
            </div>
        )
    }

    if (!detail) {
        return (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
                <div className="grid size-14 place-items-center rounded-full bg-muted">
                    <MessagesSquare className="h-7 w-7 text-muted-foreground/50" />
                </div>
                <div>
                    <p className="text-sm font-medium text-foreground/70">Select a conversation</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                        Pick a call from the list to read its transcript and listen to the recording.
                    </p>
                </div>
            </div>
        )
    }

    const name = detail.contact?.full_name

    return (
        <div className="flex min-h-0 flex-1 flex-col">
            {/* Header */}
            <div className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3.5">
                <Button
                    variant="ghost"
                    size="icon"
                    className="-ml-2 h-8 w-8 shrink-0 md:hidden"
                    onClick={onBack}
                    aria-label="Back to list"
                >
                    <ArrowLeft className="h-4 w-4" />
                </Button>
                <Avatar name={name} />
                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                        <span className={cn("truncate text-sm font-semibold", !name && "italic text-muted-foreground")}>
                            {name ?? "Unknown caller"}
                        </span>
                        {detail.is_new_patient && (
                            <span className="inline-flex shrink-0 items-center gap-1 text-[11px] font-normal text-indigo-600 dark:text-indigo-400">
                                <UserPlus className="h-3.5 w-3.5" /> New
                            </span>
                        )}
                        {detail.workflow_status && (
                            <StatusBadge status={detail.workflow_status} className="shrink-0" />
                        )}
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                        <DirectionPill direction={detail.call_direction} />
                        {detail.phone_reveal_available ? (
                            <RevealablePhone
                                callId={detail.id}
                                masked={detail.phone_masked}
                                available={detail.phone_reveal_available}
                                revealed={detail.phone_revealed}
                                className="text-xs"
                            />
                        ) : (
                            <span className="tabular-nums">{formatDateTime(detail.call_date, detail.call_time)}</span>
                        )}
                    </div>
                </div>
            </div>

            {/* Body: summary + transcript */}
            <div className="flex min-h-0 flex-1 flex-col">
                {detail.summary && (
                    <div className="shrink-0 border-b border-border bg-muted/40 px-5 py-3">
                        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                            AI Summary
                        </p>
                        <p className="text-xs leading-relaxed text-foreground/90">{detail.summary}</p>
                    </div>
                )}
                <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
                    <TranscriptSection detail={detail} fill />
                </div>
            </div>

            {/* Footer: recording player */}
            <div className="shrink-0 border-t border-border bg-card px-5 py-3">
                <RecordingSection detail={detail} compact />
            </div>

            {/* Details fold in here below xl, where the right rail is hidden */}
            <div className="max-h-72 shrink-0 overflow-y-auto border-t border-border bg-card/40 xl:hidden">
                <DetailsContent detail={detail} statuses={statuses} onResolved={onResolved} />
            </div>
        </div>
    )
}

// ── Resizable panes ───────────────────────────────────────────────────────────
//
// The three columns are a flex row: the two rails carry an explicit width and
// the centre takes what's left. The widths live in a CSS custom property rather
// than an inline `width`, so they apply only inside the responsive utility that
// reads them — below `md` the left rail is still full-width, and the details
// rail still only exists at `xl`.

const PANE_WIDTH_STORAGE_KEY = "conversation-view:pane-widths"

/** Defaults match the fixed widths this layout used before it was resizable. */
const DEFAULT_LIST_WIDTH = 320
const DEFAULT_DETAILS_WIDTH = 288

const MIN_LIST_WIDTH = 220
const MAX_LIST_WIDTH = 560
const MIN_DETAILS_WIDTH = 240
const MAX_DETAILS_WIDTH = 560
/** The transcript stops being readable below this, so a rail can't eat past it. */
const MIN_CENTER_WIDTH = 360
/** Both dividers are this wide; counted when working out the space left over. */
const DIVIDER_WIDTH = 5

/** Arrow-key step, so the dividers are usable without a pointer. */
const NUDGE_STEP = 16

function clamp(value: number, min: number, max: number): number {
    return Math.min(Math.max(value, min), max)
}

type PaneWidths = { list: number; details: number }

function readStoredWidths(): PaneWidths {
    const fallback = { list: DEFAULT_LIST_WIDTH, details: DEFAULT_DETAILS_WIDTH }
    try {
        const raw = window.localStorage.getItem(PANE_WIDTH_STORAGE_KEY)
        if (!raw) return fallback
        const parsed = JSON.parse(raw) as Partial<PaneWidths>
        return {
            list: Number.isFinite(parsed.list)
                ? clamp(parsed.list as number, MIN_LIST_WIDTH, MAX_LIST_WIDTH)
                : fallback.list,
            details: Number.isFinite(parsed.details)
                ? clamp(parsed.details as number, MIN_DETAILS_WIDTH, MAX_DETAILS_WIDTH)
                : fallback.details,
        }
    } catch {
        // Private-mode localStorage, or something else wrote the key.
        return fallback
    }
}

/** True at `xl`, the only width where the details rail is a third column.
 *  The centre-pane budget depends on whether that column is on screen. */
function useIsWideLayout(): boolean {
    const query = "(min-width: 1280px)"
    const [isWide, setIsWide] = useState(
        () => typeof window !== "undefined" && window.matchMedia(query).matches,
    )
    useEffect(() => {
        const mq = window.matchMedia(query)
        const onChange = (e: MediaQueryListEvent) => setIsWide(e.matches)
        mq.addEventListener("change", onChange)
        return () => mq.removeEventListener("change", onChange)
    }, [])
    return isWide
}

/** A draggable divider between two panes.
 *
 *  Pointer capture keeps the drag alive when the cursor outruns the 5px strip,
 *  which is most of the time. Double-click restores the default width and the
 *  arrow keys nudge it, so this is not a pointer-only control. */
function PaneDivider({
    label,
    width,
    min,
    max,
    onDragTo,
    onNudge,
    onReset,
    onCommit,
    className,
}: {
    label: string
    width: number
    min: number
    max: number
    /** Absolute pointer position; the parent turns it into a width. */
    onDragTo: (clientX: number) => void
    onNudge: (deltaPx: number) => void
    onReset: () => void
    /** Drag finished — the parent persists at this point, not on every frame. */
    onCommit: () => void
    className?: string
}) {
    const [dragging, setDragging] = useState(false)

    // While dragging, the pointer leaves the strip constantly. Setting these on
    // the body keeps the resize cursor and stops the panes selecting text.
    useEffect(() => {
        if (!dragging) return
        const { style } = document.body
        const previousCursor = style.cursor
        const previousSelect = style.userSelect
        style.cursor = "col-resize"
        style.userSelect = "none"
        return () => {
            style.cursor = previousCursor
            style.userSelect = previousSelect
        }
    }, [dragging])

    function endDrag(e: React.PointerEvent<HTMLDivElement>) {
        if (!dragging) return
        if (e.currentTarget.hasPointerCapture(e.pointerId)) {
            e.currentTarget.releasePointerCapture(e.pointerId)
        }
        setDragging(false)
        onCommit()
    }

    return (
        <div
            role="separator"
            aria-orientation="vertical"
            aria-label={label}
            aria-valuenow={Math.round(width)}
            aria-valuemin={min}
            aria-valuemax={max}
            tabIndex={0}
            onPointerDown={(e) => {
                // Left button only; a right-click here should not start a drag.
                if (e.button !== 0) return
                e.preventDefault()
                e.currentTarget.setPointerCapture(e.pointerId)
                setDragging(true)
            }}
            onPointerMove={(e) => {
                if (dragging) onDragTo(e.clientX)
            }}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onDoubleClick={onReset}
            onKeyDown={(e) => {
                if (e.key === "ArrowLeft") {
                    e.preventDefault()
                    onNudge(-NUDGE_STEP)
                    onCommit()
                } else if (e.key === "ArrowRight") {
                    e.preventDefault()
                    onNudge(NUDGE_STEP)
                    onCommit()
                } else if (e.key === "Home") {
                    e.preventDefault()
                    onReset()
                }
            }}
            className={cn(
                // 5px of grab area around a 1px line: wide enough to hit,
                // narrow enough to still read as a divider.
                "group relative z-10 flex w-[5px] shrink-0 cursor-col-resize touch-none select-none items-stretch",
                "focus-visible:outline-none",
                className,
            )}
        >
            <div
                className={cn(
                    "pointer-events-none mx-auto h-full w-px transition-colors",
                    dragging
                        ? "w-0.5 bg-primary"
                        : "bg-border group-hover:w-0.5 group-hover:bg-primary/60 group-focus-visible:w-0.5 group-focus-visible:bg-primary",
                )}
            />
        </div>
    )
}

// ── Main view ─────────────────────────────────────────────────────────────────

export function ConversationView({
    items,
    loading,
    total,
    page,
    pageCount,
    from,
    to,
    hasFilters,
    onPageChange,
    onResolved,
    statuses = [],
    title = "Conversations",
    emptyTitle = "No conversations found",
    emptyHint = "Conversations will appear here once your voice agent starts taking calls.",
}: ConversationViewProps) {
    // Only the user's explicit pick is state; the *effective* selection is
    // derived so it self-heals when the list changes (page/filter) without an
    // extra render pass — it falls back to the first row.
    const [userSelectedId, setUserSelectedId] = useState<string | null>(null)
    const [detail, setDetail] = useState<CallDetail | null>(null)
    const [mobileDetailOpen, setMobileDetailOpen] = useState(false)
    const reqRef = useRef(0)

    const selectedId =
        userSelectedId && items.some((c) => c.id === userSelectedId)
            ? userSelectedId
            : items[0]?.id ?? null

    // Loading/ready are derived from whether the fetched detail matches the
    // current selection — no effect-synced flag needed.
    const detailReady = !!detail && detail.id === selectedId
    const detailLoading = !!selectedId && !detailReady

    // Fetch detail for the selected call. A request token guards against a
    // slow earlier response overwriting a newer selection.
    useEffect(() => {
        if (!selectedId) return
        const token = ++reqRef.current
        getCall(selectedId)
            .then((d) => {
                if (reqRef.current === token) setDetail(d)
            })
            .catch((e) => {
                if (reqRef.current === token) toast.error(e instanceof Error ? e.message : "Failed to load call")
            })
    }, [selectedId])

    function refreshDetail() {
        onResolved()
        if (!selectedId) return
        const token = ++reqRef.current
        getCall(selectedId)
            .then((d) => {
                if (reqRef.current === token) setDetail(d)
            })
            .catch(() => { /* surfaced elsewhere */ })
    }

    function selectCall(id: string) {
        setUserSelectedId(id)
        setMobileDetailOpen(true)
    }

    const showDetailOnMobile = mobileDetailOpen

    // ── Pane sizing ──────────────────────────────────────────────────────────
    const containerRef = useRef<HTMLDivElement>(null)
    const isWideLayout = useIsWideLayout()
    const [paneWidths, setPaneWidths] = useState<PaneWidths>(readStoredWidths)

    const persistWidths = useCallback(() => {
        try {
            window.localStorage.setItem(
                PANE_WIDTH_STORAGE_KEY,
                JSON.stringify(paneWidths),
            )
        } catch {
            // Storage unavailable — the layout still works, it just won't stick.
        }
    }, [paneWidths])

    /** Widest the list rail may get without starving the centre pane. The
     *  details rail only counts against the budget where it is on screen. */
    const listCeiling = useCallback(
        (currentDetails: number) => {
            const container = containerRef.current?.getBoundingClientRect().width
            if (!container) return MAX_LIST_WIDTH
            const reserved =
                MIN_CENTER_WIDTH +
                DIVIDER_WIDTH +
                (isWideLayout ? currentDetails + DIVIDER_WIDTH : 0)
            return Math.min(MAX_LIST_WIDTH, container - reserved)
        },
        [isWideLayout],
    )

    const detailsCeiling = useCallback((currentList: number) => {
        const container = containerRef.current?.getBoundingClientRect().width
        if (!container) return MAX_DETAILS_WIDTH
        return Math.min(
            MAX_DETAILS_WIDTH,
            container - currentList - MIN_CENTER_WIDTH - DIVIDER_WIDTH * 2,
        )
    }, [])

    const setListWidth = useCallback(
        (next: number) => {
            setPaneWidths((prev) => {
                // Clamp low last so a cramped viewport still yields a usable
                // rail rather than a ceiling below the minimum.
                const list = clamp(
                    Math.min(next, listCeiling(prev.details)),
                    MIN_LIST_WIDTH,
                    MAX_LIST_WIDTH,
                )
                // Same width → same object, so a pointermove that changes
                // nothing costs no render and the resize observer can't loop.
                return list === prev.list ? prev : { ...prev, list }
            })
        },
        [listCeiling],
    )

    const setDetailsWidth = useCallback(
        (next: number) => {
            setPaneWidths((prev) => {
                const details = clamp(
                    Math.min(next, detailsCeiling(prev.list)),
                    MIN_DETAILS_WIDTH,
                    MAX_DETAILS_WIDTH,
                )
                return details === prev.details ? prev : { ...prev, details }
            })
        },
        [detailsCeiling],
    )

    // A width that was fine on a wide window can starve the centre pane once
    // the window shrinks, so re-apply the ceilings whenever the container
    // changes size. The updater returns `prev` when nothing moved, so this
    // cannot feed itself.
    useEffect(() => {
        const node = containerRef.current
        if (!node) return
        const observer = new ResizeObserver(() => {
            setPaneWidths((prev) => {
                const list = clamp(
                    Math.min(prev.list, listCeiling(prev.details)),
                    MIN_LIST_WIDTH,
                    MAX_LIST_WIDTH,
                )
                const details = clamp(
                    Math.min(prev.details, detailsCeiling(list)),
                    MIN_DETAILS_WIDTH,
                    MAX_DETAILS_WIDTH,
                )
                return list === prev.list && details === prev.details
                    ? prev
                    : { list, details }
            })
        })
        observer.observe(node)
        return () => observer.disconnect()
    }, [listCeiling, detailsCeiling])

    return (
        <div
            ref={containerRef}
            style={
                {
                    "--pane-list-w": `${paneWidths.list}px`,
                    "--pane-details-w": `${paneWidths.details}px`,
                } as React.CSSProperties
            }
            className="flex h-[calc(100vh-15rem)] min-h-[540px] overflow-hidden rounded-xl border bg-card shadow-sm"
        >
            {/* Left rail */}
            <div
                className={cn(
                    "w-full shrink-0 flex-col bg-card/40 md:flex md:w-[var(--pane-list-w)]",
                    showDetailOnMobile ? "hidden md:flex" : "flex",
                )}
            >
                <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
                    <div className="flex items-center gap-2 text-sm font-semibold">
                        <MessagesSquare className="h-4 w-4 text-muted-foreground" />
                        {title}
                        {!loading && <span className="text-muted-foreground">({total.toLocaleString()})</span>}
                    </div>
                </div>

                {loading ? (
                    <RowSkeletons />
                ) : items.length === 0 ? (
                    <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
                        <div className="grid size-11 place-items-center rounded-full bg-muted">
                            <Inbox className="h-5 w-5 text-muted-foreground/50" />
                        </div>
                        <p className="text-sm font-medium text-foreground/70">{emptyTitle}</p>
                        <p className="text-xs text-muted-foreground">
                            {hasFilters ? "Try adjusting or clearing your filters." : emptyHint}
                        </p>
                    </div>
                ) : (
                    <ScrollArea className="flex-1">
                        {items.map((item) => (
                            <ConversationRow
                                key={item.id}
                                item={item}
                                selected={item.id === selectedId}
                                onSelect={() => selectCall(item.id)}
                            />
                        ))}
                    </ScrollArea>
                )}

                {!loading && total > 0 && (
                    <div className="flex shrink-0 items-center justify-between gap-2 border-t border-border px-3 py-2">
                        <span className="text-[11px] tabular-nums text-muted-foreground">
                            {from}–{to} of {total.toLocaleString()}
                        </span>
                        {pageCount > 1 && (
                            <div className="flex items-center gap-1">
                                <Button
                                    variant="outline"
                                    size="icon"
                                    className="h-7 w-7"
                                    disabled={page === 0}
                                    onClick={() => onPageChange(page - 1)}
                                    aria-label="Previous page"
                                >
                                    <ChevronLeft className="h-4 w-4" />
                                </Button>
                                <span className="px-1 text-[11px] tabular-nums text-muted-foreground">
                                    {page + 1}/{pageCount}
                                </span>
                                <Button
                                    variant="outline"
                                    size="icon"
                                    className="h-7 w-7"
                                    disabled={page >= pageCount - 1}
                                    onClick={() => onPageChange(page + 1)}
                                    aria-label="Next page"
                                >
                                    <ChevronRight className="h-4 w-4" />
                                </Button>
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Only side-by-side from md up, so there is nothing to drag below it. */}
            <PaneDivider
                label="Resize conversation list"
                width={paneWidths.list}
                min={MIN_LIST_WIDTH}
                max={MAX_LIST_WIDTH}
                onDragTo={(clientX) => {
                    const rect = containerRef.current?.getBoundingClientRect()
                    if (rect) setListWidth(clientX - rect.left)
                }}
                onNudge={(delta) => setListWidth(paneWidths.list + delta)}
                onReset={() => setListWidth(DEFAULT_LIST_WIDTH)}
                onCommit={persistWidths}
                className="hidden md:flex"
            />

            {/* Center pane */}
            <div className={cn("min-w-0 flex-1 flex-col", showDetailOnMobile ? "flex" : "hidden md:flex")}>
                <CenterPane
                    detail={detailReady ? detail : null}
                    statuses={statuses}
                    loading={detailLoading}
                    onBack={() => setMobileDetailOpen(false)}
                    onResolved={refreshDetail}
                />
            </div>

            {/* The details rail only exists at xl, and so does its divider. */}
            <PaneDivider
                label="Resize details panel"
                width={paneWidths.details}
                min={MIN_DETAILS_WIDTH}
                max={MAX_DETAILS_WIDTH}
                onDragTo={(clientX) => {
                    const rect = containerRef.current?.getBoundingClientRect()
                    // Measured from the right edge — this rail grows leftwards.
                    if (rect) setDetailsWidth(rect.right - clientX)
                }}
                onNudge={(delta) => setDetailsWidth(paneWidths.details - delta)}
                onReset={() => setDetailsWidth(DEFAULT_DETAILS_WIDTH)}
                onCommit={persistWidths}
                className="hidden xl:flex"
            />

            {/* Right details pane (xl and up) */}
            <div className="hidden shrink-0 flex-col bg-card/40 xl:flex xl:w-[var(--pane-details-w)]">
                <div className="flex shrink-0 items-center border-b border-border px-4 py-3 text-sm font-semibold">
                    Details
                </div>
                {detailReady ? (
                    <DetailsPane detail={detail} statuses={statuses} onResolved={refreshDetail} />
                ) : (
                    <div className="flex flex-1 items-center justify-center p-6 text-center">
                        <p className="text-xs text-muted-foreground">
                            {detailLoading ? "Loading…" : "No call selected."}
                        </p>
                    </div>
                )}
            </div>
        </div>
    )
}
