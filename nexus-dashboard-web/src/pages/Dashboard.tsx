import { useEffect, useState, useCallback } from "react"
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

// ── Volume card ───────────────────────────────────────────────────────────────

interface VolumeCardProps {
    label: string
    value: number | undefined
    icon: React.ElementType
    loading: boolean
}

function VolumeCard({ label, value, icon: Icon, loading }: VolumeCardProps) {
    return (
        <Card>
            {loading ? (
                <CardContent className="p-6 space-y-3">
                    <Skeleton className="h-4 w-24" />
                    <Skeleton className="h-8 w-16" />
                </CardContent>
            ) : (
                <>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">{label}</CardTitle>
                        <div className="rounded-md bg-primary/10 p-2 text-primary">
                            <Icon className="h-4 w-4" />
                        </div>
                    </CardHeader>
                    <CardContent>
                        <div className="text-3xl font-bold tabular-nums">{value ?? 0}</div>
                    </CardContent>
                </>
            )}
        </Card>
    )
}

// ── Callback queue item ───────────────────────────────────────────────────────

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
        <div className="rounded-lg border bg-card p-4 space-y-2">
            <div className="flex items-start justify-between gap-2">
                <div>
                    <p className="font-medium text-sm">
                        {item.contact_name ?? <span className="text-muted-foreground">Unknown caller</span>}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                        {formatDate(item.call_date)} · {formatTime(item.call_time)}
                        {item.call_duration_seconds ? ` · ${formatDuration(item.call_duration_seconds)}` : ""}
                    </p>
                </div>
                <Button
                    variant="ghost"
                    size="sm"
                    className="text-xs gap-1 shrink-0"
                    onClick={() => setOpen((o) => !o)}
                >
                    {open ? "Cancel" : "Resolve"}
                </Button>
            </div>

            {item.summary && (
                <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">
                    {item.summary}
                </p>
            )}

            {open && (
                <div className="space-y-2 pt-1">
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

    const callbackQueue = summary?.callback_queue ?? []
    const tagCounts = summary?.tag_counts ?? []

    return (
        <div className="flex-1 space-y-6 p-8 pt-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">
                        {greeting}{user?.email ? `, ${user.email.split("@")[0]}` : ""}
                    </h2>
                    <p className="text-muted-foreground">
                        Here's your call activity summary.
                    </p>
                </div>
                <Button
                    variant="outline"
                    onClick={fetchSummary}
                    disabled={loading}
                    className="gap-2"
                >
                    <RefreshCcw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            {/* Volume cards */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <VolumeCard label="Today" value={summary?.call_volume.today} icon={CalendarDays} loading={loading} />
                <VolumeCard label="This Week" value={summary?.call_volume.this_week} icon={TrendingUp} loading={loading} />
                <VolumeCard label="This Month" value={summary?.call_volume.this_month} icon={Phone} loading={loading} />
                <VolumeCard label="All Time" value={summary?.call_volume.all_time} icon={Infinity} loading={loading} />
            </div>

            {/* Bottom grid: tag breakdown + callback queue */}
            <div className="grid gap-6 lg:grid-cols-2">
                {/* Tag breakdown */}
                <Card>
                    <CardHeader>
                        <CardTitle>Call Tags Breakdown</CardTitle>
                        <CardDescription>Count of all-time calls by primary tag.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        {loading ? (
                            <div className="space-y-3">
                                {Array.from({ length: 5 }).map((_, i) => (
                                    <div key={i} className="flex items-center gap-3">
                                        <Skeleton className="h-5 w-28 rounded-full" />
                                        <Skeleton className="h-3 flex-1" />
                                        <Skeleton className="h-4 w-8" />
                                    </div>
                                ))}
                            </div>
                        ) : tagCounts.length === 0 ? (
                            <p className="text-sm text-muted-foreground text-center py-6">No calls recorded yet.</p>
                        ) : (
                            <div className="space-y-3">
                                {tagCounts.map((tc) => {
                                    const colorClass = STATUS_COLOR_MAP[tc.tag] ?? "bg-zinc-100 text-zinc-600 border-zinc-200"
                                    const maxCount = tagCounts[0]?.count ?? 1
                                    const pct = Math.round((tc.count / maxCount) * 100)
                                    return (
                                        <div key={tc.tag} className="flex items-center gap-3">
                                            <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium w-36 shrink-0 ${colorClass}`}>
                                                {tc.label}
                                            </span>
                                            <div className="flex-1 bg-muted rounded-full h-2 overflow-hidden">
                                                <div
                                                    className="h-2 bg-primary/60 rounded-full transition-all"
                                                    style={{ width: `${pct}%` }}
                                                />
                                            </div>
                                            <span className="text-sm font-medium tabular-nums w-8 text-right">{tc.count}</span>
                                        </div>
                                    )
                                })}
                            </div>
                        )}

                        <div className="mt-4 pt-4 border-t">
                            <Link to="/calls">
                                <Button variant="ghost" size="sm" className="gap-1.5 text-xs">
                                    View all calls <ArrowRight className="h-3 w-3" />
                                </Button>
                            </Link>
                        </div>
                    </CardContent>
                </Card>

                {/* Callback queue */}
                <Card>
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div>
                                <CardTitle className="flex items-center gap-2">
                                    <Clock className="h-4 w-4 text-amber-500" />
                                    Needs Callback
                                    {callbackQueue.length > 0 && (
                                        <Badge variant="destructive" className="text-xs">
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
                                    <Skeleton key={i} className="h-20 w-full rounded-lg" />
                                ))}
                            </div>
                        ) : callbackQueue.length === 0 ? (
                            <div className="flex flex-col items-center justify-center py-8 text-center text-muted-foreground">
                                <CheckCircle2 className="h-10 w-10 mb-3 text-green-400" />
                                <p className="font-medium text-sm">All caught up!</p>
                                <p className="text-xs mt-1">No pending callbacks.</p>
                            </div>
                        ) : (
                            <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
                                {callbackQueue.map((item) => (
                                    <QueueItem key={item.call_id} item={item} onResolved={fetchSummary} />
                                ))}
                            </div>
                        )}

                        {callbackQueue.length > 0 && (
                            <div className="mt-4 pt-4 border-t">
                                <Link to="/calls?tags=needs_callback">
                                    <Button variant="ghost" size="sm" className="gap-1.5 text-xs">
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
            <div className="flex gap-3 flex-wrap">
                <Link to="/calls">
                    <Button variant="outline" className="gap-2">
                        <PhoneIncoming className="h-4 w-4" /> All Calls
                    </Button>
                </Link>
                <Link to="/calls" state={{ tags: ["appointment_booked"] }}>
                    <Button variant="outline" className="gap-2">
                        <PhoneOutgoing className="h-4 w-4" /> Booked Today
                    </Button>
                </Link>
            </div>
        </div>
    )
}
