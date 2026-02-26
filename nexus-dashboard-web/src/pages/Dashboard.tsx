import { useEffect, useState, useCallback, useRef } from "react"
import { Link } from "react-router-dom"
import {
    Phone,
    PhoneIncoming,
    PhoneOutgoing,
    CheckCircle2,
    AlertCircle,
    RefreshCcw,
    ArrowRight,
    CalendarDays,
    TrendingUp,
    Infinity,
    Clock,
} from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Input } from "@/components/ui/input"
import { toast } from "sonner"
import { useAuth } from "@/context/AuthContext"
import type { DashboardSummary, CallbackQueueItem } from "@/types"
import { getDashboardSummary } from "@/lib/dashboard-api"
import { resolveCallback } from "@/lib/calls-api"
import { STATUS_OPTIONS } from "@/pages/Calls"

// ── Constants ─────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 30_000

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTime(timeStr: string | null): string {
    if (!timeStr) return "—"
    const [h, m] = timeStr.split(":")
    const hour = parseInt(h, 10)
    const ampm = hour >= 12 ? "PM" : "AM"
    const h12 = hour % 12 || 12
    return `${h12}:${m} ${ampm}`
}

function formatDate(dateStr: string | null): string {
    if (!dateStr) return "—"
    return new Date(dateStr).toLocaleDateString("en-US", { month: "short", day: "numeric" })
}

function formatDuration(seconds: number | null): string {
    if (seconds === null) return ""
    if (seconds < 60) return `${seconds}s`
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return s > 0 ? `${m}m ${s}s` : `${m}m`
}

const STATUS_COLOR_MAP = Object.fromEntries(
    STATUS_OPTIONS.map((o) => [o.value, o.color])
)

// Semantic bar colors for tag breakdown (dark-mode safe)
const TAG_BAR_COLOR: Record<string, string> = {
    appointment_booked: "bg-emerald-500",
    appointment_rescheduled: "bg-blue-500",
    appointment_cancelled: "bg-zinc-500",
    emergency: "bg-red-500",
    complaint: "bg-orange-500",
    needs_callback: "bg-amber-500",
    faq_handled: "bg-sky-500",
    financial_inquiry: "bg-violet-500",
    transferred: "bg-teal-500",
    insurance_verified: "bg-green-500",
    insurance_unverified: "bg-rose-500",
    no_action_needed: "bg-zinc-400",
}

// Per-card icon gradient for volume cards
const VOLUME_CARD_CONFIG = [
    {
        label: "Today",
        key: "today" as const,
        icon: CalendarDays,
        iconBg: "bg-blue-500/15 dark:bg-blue-500/20",
        iconColor: "text-blue-500",
    },
    {
        label: "This Week",
        key: "this_week" as const,
        icon: TrendingUp,
        iconBg: "bg-violet-500/15 dark:bg-violet-500/20",
        iconColor: "text-violet-500",
    },
    {
        label: "This Month",
        key: "this_month" as const,
        icon: Phone,
        iconBg: "bg-purple-500/15 dark:bg-purple-500/20",
        iconColor: "text-purple-500",
    },
    {
        label: "All Time",
        key: "all_time" as const,
        icon: Infinity,
        iconBg: "bg-slate-500/15 dark:bg-slate-400/15",
        iconColor: "text-slate-400",
    },
]

// ── Animated Count Hook ───────────────────────────────────────────────────────

function useAnimatedCount(target: number | undefined, duration = 600): number {
    const [displayed, setDisplayed] = useState(0)
    const frameRef = useRef<number | null>(null)
    const startRef = useRef<number | null>(null)
    const fromRef = useRef(0)

    useEffect(() => {
        if (target === undefined) return
        const from = fromRef.current
        const to = target

        if (frameRef.current) cancelAnimationFrame(frameRef.current)
        startRef.current = null

        function tick(timestamp: number) {
            if (!startRef.current) startRef.current = timestamp
            const elapsed = timestamp - startRef.current
            const progress = Math.min(elapsed / duration, 1)
            // ease-out cubic
            const eased = 1 - Math.pow(1 - progress, 3)
            setDisplayed(Math.round(from + (to - from) * eased))
            if (progress < 1) {
                frameRef.current = requestAnimationFrame(tick)
            } else {
                fromRef.current = to
            }
        }

        frameRef.current = requestAnimationFrame(tick)
        return () => { if (frameRef.current) cancelAnimationFrame(frameRef.current) }
    }, [target, duration])

    return displayed
}

// ── Volume Card ───────────────────────────────────────────────────────────────

interface VolumeCardProps {
    label: string
    value: number | undefined
    icon: React.ElementType
    iconBg: string
    iconColor: string
    loading: boolean
}

function VolumeCard({ label, value, icon: Icon, iconBg, iconColor, loading }: VolumeCardProps) {
    const animatedValue = useAnimatedCount(loading ? undefined : (value ?? 0))

    if (loading) {
        return (
            <Card className="p-6 space-y-4">
                <div className="flex items-center justify-between">
                    <Skeleton className="h-4 w-20" />
                    <Skeleton className="h-9 w-9 rounded-lg" />
                </div>
                <Skeleton className="h-10 w-16" />
            </Card>
        )
    }

    return (
        <Card className="group relative overflow-hidden transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/20 cursor-default">
            {/* Subtle top gradient accent */}
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/30 to-transparent" />
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
                <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
                <div className={`rounded-lg p-2.5 ${iconBg} transition-transform duration-200 group-hover:scale-110`}>
                    <Icon className={`h-4 w-4 ${iconColor}`} />
                </div>
            </CardHeader>
            <CardContent className="pb-5">
                <div className="text-4xl font-black tabular-nums tracking-tight animate-count-fade">
                    {animatedValue.toLocaleString()}
                </div>
                <p className="text-xs text-muted-foreground/60 mt-1">calls</p>
            </CardContent>
        </Card>
    )
}

// ── Callback Queue Item ────────────────────────────────────────────────────────

interface QueueItemProps {
    item: CallbackQueueItem
    onResolved: () => void
}

function QueueItem({ item, onResolved }: QueueItemProps) {
    const [open, setOpen] = useState(false)
    const [note, setNote] = useState("")
    const [resolving, setResolving] = useState(false)

    async function handleResolve() {
        setResolving(true)
        try {
            await resolveCallback(item.call_id, note || undefined)
            toast.success("Callback resolved")
            onResolved()
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to resolve")
        } finally {
            setResolving(false)
        }
    }

    return (
        <div className="rounded-lg border border-border/60 bg-card hover:bg-muted/20 transition-colors duration-150 p-3.5 space-y-2">
            <div className="flex items-start justify-between gap-2">
                <div className="flex items-start gap-2.5 min-w-0">
                    {/* Urgency dot */}
                    <span className="mt-1 h-1.5 w-1.5 rounded-full bg-amber-500 shrink-0" />
                    <div className="min-w-0">
                        <p className="font-medium text-sm truncate">
                            {item.contact_name ?? <span className="text-muted-foreground italic">Unknown caller</span>}
                        </p>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            {formatDate(item.call_date)} · {formatTime(item.call_time)}
                            {item.call_duration_seconds ? ` · ${formatDuration(item.call_duration_seconds)}` : ""}
                        </p>
                    </div>
                </div>
                <Button
                    variant={open ? "ghost" : "secondary"}
                    size="sm"
                    className="text-xs gap-1 shrink-0 h-7"
                    onClick={() => setOpen((o) => !o)}
                >
                    {open ? "Cancel" : "Resolve"}
                </Button>
            </div>

            {item.summary && (
                <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed pl-4">
                    {item.summary}
                </p>
            )}

            {open && (
                <div className="space-y-2 pt-1 pl-4">
                    <Input
                        placeholder="Resolution note (optional)…"
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        className="text-xs h-8"
                    />
                    <Button
                        size="sm"
                        className="gap-1.5 w-full"
                        onClick={handleResolve}
                        disabled={resolving}
                    >
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        {resolving ? "Resolving…" : "Mark Resolved"}
                    </Button>
                </div>
            )}
        </div>
    )
}

// ── Animated Tag Bar ──────────────────────────────────────────────────────────

interface TagBarProps {
    tag: string
    label: string
    count: number
    total: number
    pct: number
    colorClass: string
    barColor: string
}

function TagBar({ label, count, total, pct, colorClass, barColor }: TagBarProps) {
    const [width, setWidth] = useState(0)

    useEffect(() => {
        // Small timeout to ensure CSS transition fires
        const id = setTimeout(() => setWidth(pct), 60)
        return () => clearTimeout(id)
    }, [pct])

    const countPct = total > 0 ? Math.round((count / total) * 100) : 0

    return (
        <div className="flex items-center gap-3">
            <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium w-40 shrink-0 truncate ${colorClass}`}>
                {label}
            </span>
            <div className="flex-1 bg-muted rounded-full h-2.5 overflow-hidden">
                <div
                    className={`h-2.5 rounded-full transition-all duration-700 ease-out ${barColor}`}
                    style={{ width: `${width}%` }}
                />
            </div>
            <div className="flex items-center gap-1.5 w-16 justify-end shrink-0">
                <span className="text-sm font-semibold tabular-nums text-foreground">{count}</span>
                <span className="text-xs text-muted-foreground">({countPct}%)</span>
            </div>
        </div>
    )
}

// ── Dashboard Page ────────────────────────────────────────────────────────────

export default function Dashboard() {
    const { user } = useAuth()
    const [summary, setSummary] = useState<DashboardSummary | null>(null)
    const [loading, setLoading] = useState(true)

    const fetchSummary = useCallback(async () => {
        try {
            const data = await getDashboardSummary()
            setSummary(data)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load dashboard"
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { fetchSummary() }, [fetchSummary])

    // 30-second auto-poll
    useEffect(() => {
        const id = setInterval(fetchSummary, POLL_INTERVAL_MS)
        return () => clearInterval(id)
    }, [fetchSummary])

    const hour = new Date().getHours()
    const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"
    const todayStr = new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })

    const callbackQueue = summary?.callback_queue ?? []
    const tagCounts = summary?.tag_counts ?? []
    const hasCallbacks = callbackQueue.length > 0

    const totalTagCount = tagCounts.reduce((sum, tc) => sum + tc.count, 0)

    return (
        <div className="flex-1 space-y-6 p-8 pt-6 animate-fade-in-up">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-2xl font-bold tracking-tight">
                        {greeting}{user?.email ? `, ${user.email.split("@")[0]}` : ""}
                    </h2>
                    <p className="text-sm text-muted-foreground mt-0.5">
                        {todayStr} · Here's your call activity overview.
                    </p>
                </div>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={fetchSummary}
                    disabled={loading}
                    className="gap-2 h-8 text-xs"
                >
                    <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            {/* Volume cards */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                {VOLUME_CARD_CONFIG.map(({ label, key, icon, iconBg, iconColor }) => (
                    <VolumeCard
                        key={key}
                        label={label}
                        value={summary?.call_volume[key]}
                        icon={icon}
                        iconBg={iconBg}
                        iconColor={iconColor}
                        loading={loading}
                    />
                ))}
            </div>

            {/* Bottom grid: tag breakdown + callback queue */}
            <div className="grid gap-6 lg:grid-cols-2">
                {/* Tag breakdown */}
                <Card>
                    <CardHeader className="pb-4">
                        <CardTitle className="text-base font-semibold">Call Tags Breakdown</CardTitle>
                        <CardDescription>All-time calls by primary tag.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        {loading ? (
                            <div className="space-y-4">
                                {Array.from({ length: 5 }).map((_, i) => (
                                    <div key={i} className="flex items-center gap-3">
                                        <Skeleton className="h-5 w-40 rounded-full" />
                                        <Skeleton className="h-2.5 flex-1 rounded-full" />
                                        <Skeleton className="h-4 w-16" />
                                    </div>
                                ))}
                            </div>
                        ) : tagCounts.length === 0 ? (
                            <div className="flex flex-col items-center justify-center py-10 text-center text-muted-foreground gap-2">
                                <Phone className="h-8 w-8 opacity-20" />
                                <p className="text-sm font-medium">No calls recorded yet.</p>
                                <p className="text-xs">Tags will appear here once your agent handles calls.</p>
                            </div>
                        ) : (
                            <div className="space-y-3.5">
                                {tagCounts.map((tc) => {
                                    const colorClass = STATUS_COLOR_MAP[tc.tag] ?? "bg-zinc-100 text-zinc-600 border-zinc-200"
                                    const barColor = TAG_BAR_COLOR[tc.tag] ?? "bg-primary/70"
                                    const maxCount = tagCounts[0]?.count ?? 1
                                    const pct = Math.round((tc.count / maxCount) * 100)
                                    return (
                                        <TagBar
                                            key={tc.tag}
                                            tag={tc.tag}
                                            label={tc.label}
                                            count={tc.count}
                                            total={totalTagCount}
                                            pct={pct}
                                            colorClass={colorClass}
                                            barColor={barColor}
                                        />
                                    )
                                })}
                            </div>
                        )}

                        <div className="mt-5 pt-4 border-t">
                            <Link to="/calls">
                                <Button variant="ghost" size="sm" className="gap-1.5 text-xs h-7">
                                    View all calls <ArrowRight className="h-3 w-3" />
                                </Button>
                            </Link>
                        </div>
                    </CardContent>
                </Card>

                {/* Callback queue */}
                <Card className={`transition-all duration-300 ${hasCallbacks ? "border-l-4 border-l-amber-500" : ""}`}>
                    <CardHeader className="pb-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <CardTitle className="text-base font-semibold flex items-center gap-2">
                                    <Clock className={`h-4 w-4 ${hasCallbacks ? "text-amber-500" : "text-muted-foreground"}`} />
                                    Needs Callback
                                    {hasCallbacks && (
                                        <Badge
                                            variant="destructive"
                                            className="text-[10px] h-5 px-1.5 font-semibold"
                                        >
                                            {callbackQueue.length}
                                        </Badge>
                                    )}
                                </CardTitle>
                                <CardDescription>Unresolved callback requests, oldest first.</CardDescription>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent>
                        {loading ? (
                            <div className="space-y-3">
                                {Array.from({ length: 3 }).map((_, i) => (
                                    <Skeleton key={i} className="h-16 w-full rounded-lg" />
                                ))}
                            </div>
                        ) : callbackQueue.length === 0 ? (
                            <div className="flex flex-col items-center justify-center py-8 text-center gap-3">
                                <div className="h-12 w-12 rounded-full bg-green-500/10 flex items-center justify-center">
                                    <CheckCircle2 className="h-6 w-6 text-green-500" />
                                </div>
                                <div>
                                    <p className="font-medium text-sm text-foreground">All caught up!</p>
                                    <p className="text-xs text-muted-foreground mt-0.5">No pending callbacks right now.</p>
                                </div>
                                <Link to="/calls">
                                    <Button variant="outline" size="sm" className="gap-1.5 text-xs h-7 mt-1">
                                        View all calls <ArrowRight className="h-3 w-3" />
                                    </Button>
                                </Link>
                            </div>
                        ) : (
                            <div className="space-y-2 max-h-[400px] overflow-y-auto pr-1 -mr-1">
                                {callbackQueue.map((item) => (
                                    <QueueItem key={item.call_id} item={item} onResolved={fetchSummary} />
                                ))}
                            </div>
                        )}

                        {hasCallbacks && (
                            <div className="mt-4 pt-4 border-t">
                                <Link to="/calls?tags=needs_callback">
                                    <Button variant="ghost" size="sm" className="gap-1.5 text-xs h-7">
                                        <AlertCircle className="h-3 w-3 text-amber-500" />
                                        View all callbacks <ArrowRight className="h-3 w-3" />
                                    </Button>
                                </Link>
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>

            {/* Quick links */}
            <div className="flex gap-2 flex-wrap">
                <Link to="/calls">
                    <Button variant="outline" size="sm" className="gap-2 h-8 text-xs">
                        <PhoneIncoming className="h-3.5 w-3.5" /> All Calls
                    </Button>
                </Link>
                <Link to="/calls" state={{ tags: ["appointment_booked"] }}>
                    <Button variant="outline" size="sm" className="gap-2 h-8 text-xs">
                        <PhoneOutgoing className="h-3.5 w-3.5" /> Booked Today
                    </Button>
                </Link>
            </div>
        </div>
    )
}
