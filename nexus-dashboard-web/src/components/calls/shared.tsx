/**
 * Shared, presentational + reveal-gated building blocks for the Calls surface.
 *
 * These were originally defined inside `pages/Calls.tsx`. They are extracted
 * here so both the table+modal view and the conversation (inbox) view render
 * identical badges, transcript bubbles, and — crucially — share the *single*
 * audit-logged PHI reveal flow. Do not duplicate the reveal logic elsewhere.
 */

import { useEffect, useState } from "react"
import { Eye, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { toast } from "sonner"
import { revealCustomPhiField, revealRecording, revealTranscript } from "@/lib/calls-api"
import { STATUS_OPTIONS } from "@/lib/constants"
import { statusBadgeClasses, statusSwatchClass } from "@/lib/status-colors"
import { cn } from "@/lib/utils"
import type { CallDetail, CustomFieldValue, TranscriptTurn, WorkflowStatus, WorkflowStatusRef } from "@/types"

const STATUS_MAP = Object.fromEntries(STATUS_OPTIONS.map((o) => [o.value, o]))

// ── Badges ──────────────────────────────────────────────────────────────────

export function TagBadge({ tag }: { tag: string }) {
    const opt = STATUS_MAP[tag]
    const cls = opt?.color ?? "bg-zinc-500/15 text-zinc-500 border-zinc-500/25"
    const label = opt?.label ?? tag.replace(/_/g, " ")
    return (
        <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${cls}`}>
            {label}
        </span>
    )
}

export function SentimentBadge({ sentiment }: { sentiment: string | null }) {
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

/** A colored chip for an assigned workflow status (human, tenant-defined). */
export function StatusBadge({ status, className }: { status: WorkflowStatusRef; className?: string }) {
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
                statusBadgeClasses(status.color),
                className,
            )}
        >
            <span className={cn("h-1.5 w-1.5 rounded-full", statusSwatchClass(status.color))} />
            {status.name}
        </span>
    )
}

const NO_STATUS = "__none__"

/**
 * Assign control — a colored Select over the institution's active statuses,
 * with a "No status" option. Calls ``onChange`` with the chosen id or null.
 */
export function StatusSelect({
    statuses,
    value,
    onChange,
    saving = false,
    className,
}: {
    statuses: WorkflowStatus[]
    value: string | null
    onChange: (statusId: string | null) => void
    saving?: boolean
    className?: string
}) {
    return (
        <Select
            value={value ?? NO_STATUS}
            onValueChange={(v) => onChange(v === NO_STATUS ? null : v)}
            disabled={saving}
        >
            <SelectTrigger className={cn("h-8 w-full text-xs", className)}>
                {saving ? (
                    <span className="flex items-center gap-1.5 text-muted-foreground">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving…
                    </span>
                ) : (
                    <SelectValue placeholder="Set status" />
                )}
            </SelectTrigger>
            <SelectContent>
                <SelectItem value={NO_STATUS}>
                    <span className="flex items-center gap-2 text-muted-foreground">
                        <span className="h-2 w-2 rounded-full border border-muted-foreground/40" />
                        No status
                    </span>
                </SelectItem>
                {statuses.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                        <span className="flex items-center gap-2">
                            <span className={cn("h-2 w-2 rounded-full", statusSwatchClass(s.color))} />
                            {s.name}
                        </span>
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    )
}

// ── Custom fields ─────────────────────────────────────────────────────────────

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

function CustomFieldDisplay({ callId, field }: { callId: string; field: CustomFieldValue }) {
    const [revealed, setRevealed] = useState(false)
    const [revealedValue, setRevealedValue] = useState<string | null>(null)
    const [revealing, setRevealing] = useState(false)

    async function handleReveal() {
        setRevealing(true)
        try {
            const result = await revealCustomPhiField(callId, field.field_key)
            setRevealedValue(result.value)
            setRevealed(true)
            toast.success("Field revealed and audited")
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to reveal field")
        } finally {
            setRevealing(false)
        }
    }

    if (field.is_phi && field.reveal_available && !revealed) {
        return (
            <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-1 h-7 gap-1.5 px-2 text-xs"
                onClick={handleReveal}
                disabled={revealing}
            >
                {revealing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Eye className="h-3 w-3" />}
                Reveal
            </Button>
        )
    }

    return (
        <p className="font-medium mt-0.5">
            {renderFieldValue({ ...field, value: revealed ? revealedValue : field.value })}
        </p>
    )
}

export function CustomFieldsSection({ callId, fields }: { callId: string; fields: CustomFieldValue[] }) {
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
                        <CustomFieldDisplay callId={callId} field={f} />
                    </div>
                ))}
            </div>
        </div>
    )
}

// ── Transcript chat bubbles ─────────────────────────────────────────────────

export function TranscriptChatBubbles({ turns }: { turns: TranscriptTurn[] }) {
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

// ── Reveal-gated transcript ─────────────────────────────────────────────────

/**
 * Audit-logged transcript reveal. `fill` makes the bubble area grow to fill its
 * parent (conversation view); the default compact box (max-h-64) is used inside
 * the detail modal.
 */
export function TranscriptSection({ detail, fill = false }: { detail: CallDetail; fill?: boolean }) {
    const [turns, setTurns] = useState<TranscriptTurn[] | null>(null)
    const [revealing, setRevealing] = useState(false)

    useEffect(() => {
        setTurns(null)
        setRevealing(false)
    }, [detail.id])

    if (!detail.transcript_available) {
        if (!fill) return null
        return (
            <div className="flex flex-1 items-center justify-center p-6 text-center">
                <p className="text-xs text-muted-foreground">No transcript was captured for this call.</p>
            </div>
        )
    }

    async function handleReveal() {
        setRevealing(true)
        try {
            const result = await revealTranscript(detail.id)
            setTurns(result.transcript_with_tool_calls ?? [])
            toast.success("Transcript revealed and audited")
        } catch (e) {
            const detailMsg =
                (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            toast.error(
                detailMsg ?? (e instanceof Error ? e.message : "Failed to reveal transcript"),
            )
        } finally {
            setRevealing(false)
        }
    }

    const gate = (
        <div className="flex min-h-24 flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
            <p className="text-xs text-muted-foreground max-w-xs">
                This transcript is PII-scrubbed and encrypted at rest. Each reveal is audit-logged.
            </p>
            <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8 gap-1.5"
                onClick={handleReveal}
                disabled={revealing}
            >
                {revealing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Eye className="h-3.5 w-3.5" />}
                Reveal transcript
            </Button>
        </div>
    )

    if (fill) {
        // Bubble area fills the conversation center pane; parent owns the scroll.
        return turns ? <TranscriptChatBubbles turns={turns} /> : gate
    }

    return (
        <div>
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1.5">
                Transcript
                <span className="ml-1.5 text-[9px] opacity-60 font-normal normal-case">
                    HIPAA ✓ PII-scrubbed
                </span>
            </p>
            <div className="rounded-lg border bg-muted max-h-64 overflow-y-auto">
                {turns ? <TranscriptChatBubbles turns={turns} /> : gate}
            </div>
        </div>
    )
}

// ── Reveal-gated recording ──────────────────────────────────────────────────

export function RecordingSection({ detail, compact = false }: { detail: CallDetail; compact?: boolean }) {
    const [recordingUrl, setRecordingUrl] = useState<string | null>(null)
    const [revealing, setRevealing] = useState(false)

    useEffect(() => {
        setRecordingUrl(null)
        setRevealing(false)
    }, [detail.id])

    if (!detail.recording_available && !recordingUrl) return null

    async function handleRevealRecording() {
        setRevealing(true)
        try {
            const result = await revealRecording(detail.id)
            setRecordingUrl(result.recording_url)
            if (result.recording_url) {
                toast.success("Recording revealed and audited")
            } else {
                toast.info("No recording is available for this call")
            }
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to reveal recording")
        } finally {
            setRevealing(false)
        }
    }

    const player = recordingUrl ? (
        <audio controls className="w-full h-10 outline-none" src={recordingUrl}>
            Your browser does not support the audio element.
        </audio>
    ) : (
        <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 gap-1.5"
            onClick={handleRevealRecording}
            disabled={revealing}
        >
            {revealing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Eye className="h-3.5 w-3.5" />}
            Reveal recording
        </Button>
    )

    if (compact) {
        return (
            <div className={cn("flex items-center justify-center", !recordingUrl && "py-1")}>
                {player}
            </div>
        )
    }

    return (
        <div>
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide mb-1">Call Recording</p>
            <div className="rounded-lg border bg-muted p-3 flex items-center justify-center">{player}</div>
        </div>
    )
}
