/**
 * Conversation (inbox) view for Calls — a secondary layout to the table+modal.
 *
 * Three panes: a conversation list rail, a center transcript/recording pane,
 * and a right details/actions pane. It reuses the shared badges, transcript
 * bubbles, and — importantly — the same audit-logged reveal flow as the modal
 * (see ./shared). No PHI is shown until explicitly revealed.
 */

import { useEffect, useRef, useState } from "react"
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
import { formatDateTime, formatDuration, formatListTimestamp, getInitials } from "./format"

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
                    <span className={cn("truncate text-sm", name ? "font-medium" : "italic text-muted-foreground")}>
                        {name ?? "Unknown caller"}
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

    return (
        <div className="flex h-[calc(100vh-15rem)] min-h-[540px] overflow-hidden rounded-xl border bg-card shadow-sm">
            {/* Left rail */}
            <div
                className={cn(
                    "w-full shrink-0 flex-col border-r border-border bg-card/40 md:flex md:w-[19rem] lg:w-[20rem]",
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

            {/* Right details pane (xl and up) */}
            <div className="hidden w-[18rem] shrink-0 flex-col border-l border-border bg-card/40 xl:flex">
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
