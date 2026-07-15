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
    Clock,
    Users,
    Percent,
    Timer,
    MapPin,
    Activity,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Input } from "@/components/ui/input"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { toast } from "sonner"
import { useAuth } from "@/context/AuthContext"
import { useSSE } from "@/hooks/useSSE"
import type { DashboardSummary, CallbackQueueItem } from "@/types"
import { getInitials } from "@/components/calls/format"
import { getDashboardSummary, getAggregateDashboard } from "@/lib/dashboard-api"
import { resolveCallback } from "@/lib/calls-api"
import { STATUS_OPTIONS } from "@/lib/constants"
import { DateRangePicker } from "@/components/dashboard/DateRangePicker"
import { RevealablePhone } from "@/components/RevealablePhone"
import { lastNDaysRange, type DateRangeValue } from "@/lib/date-range"

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
    const rounded = Math.round(seconds)
    if (rounded < 60) return `${rounded}s`
    const m = Math.floor(rounded / 60)
    const s = rounded % 60
    return s > 0 ? `${m}m ${s}s` : `${m}m`
}

// ── Volume Card Configs ──────────────────────────────────────────────────────

// Range-scoped cards — driven by the date-range picker, sourced from summary.range.
const RANGE_CARD_CONFIG = [
    { label: "Total Calls", key: "total_calls" as const, icon: Phone, accentColor: "violet", glowRgb: "139,92,246" },
    { label: "Appointments Booked", key: "appointments_booked" as const, icon: CalendarDays, accentColor: "emerald", glowRgb: "16,185,129" },
    { label: "New Patients", key: "new_patients" as const, icon: Users, accentColor: "sky", glowRgb: "14,165,233" },
    { label: "Booking Rate", key: "booking_rate" as const, icon: Percent, accentColor: "amber", glowRgb: "245,158,11", suffix: "%" },
]

const METRIC_CARDS_CONFIG = [
    {
        label: "Appointments Booked",
        key: "appointments_booked_month" as const,
        icon: CalendarDays,
        accentColor: "emerald",
        glowRgb: "16,185,129",
    },
    {
        label: "New Patients",
        key: "new_patients_month" as const,
        icon: Users,
        accentColor: "sky",
        glowRgb: "14,165,233",
    },
    {
        label: "Booking Rate",
        key: "booking_rate_month" as const,
        icon: Percent,
        accentColor: "amber",
        glowRgb: "245,158,11",
    },
    {
        label: "Avg Call Duration",
        key: "avg_call_duration_seconds" as const,
        icon: Timer,
        accentColor: "violet",
        glowRgb: "139,92,246",
    },
]

const STATUS_COLOR_MAP = Object.fromEntries(
    STATUS_OPTIONS.map((o) => [o.value, o.color])
)

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

const TAG_BAR_GLOW: Record<string, string> = {
    appointment_booked: "shadow-emerald-500/30",
    appointment_rescheduled: "shadow-blue-500/30",
    emergency: "shadow-red-500/30",
    complaint: "shadow-orange-500/30",
    needs_callback: "shadow-amber-500/30",
}

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

// ── Glass Card ───────────────────────────────────────────────────────────────

interface GlassCardProps {
    label: string
    value: number | undefined
    icon: React.ElementType
    accentColor: string
    glowRgb: string
    loading: boolean
    suffix?: string
    formatValue?: (val: number) => string
}

function GlassCard({
    label,
    value,
    icon: Icon,
    glowRgb,
    loading,
    suffix = "",
    formatValue,
}: GlassCardProps) {
    const animatedValue = useAnimatedCount(loading ? undefined : (value ?? 0))

    if (loading) {
        return (
            <div className="relative rounded-2xl border border-border/60 bg-card p-6 space-y-4">
                <div className="flex items-center justify-between">
                    <Skeleton className="h-4 w-20" />
                    <Skeleton className="h-9 w-9 rounded-xl" />
                </div>
                <Skeleton className="h-12 w-24" />
            </div>
        )
    }

    return (
        <div className="group relative overflow-hidden rounded-2xl bg-gradient-to-br from-card via-card to-accent/30 border border-border/60 shadow-sm transition-all duration-300 ease-out hover:-translate-y-1 hover:shadow-lg cursor-default">
            {/* Radial glow */}
            <div
                className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-40 h-40 rounded-full opacity-[0.08] blur-3xl transition-opacity duration-300 group-hover:opacity-[0.15]"
                style={{ background: `radial-gradient(circle, rgba(${glowRgb}, 0.8) 0%, transparent 70%)` }}
            />

            {/* Top edge highlight */}
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/20 to-transparent" />

            <div className="relative p-6">
                <div className="flex items-center justify-between mb-5">
                    <span className="text-sm font-medium text-muted-foreground">{label}</span>
                    <div className="grid shrink-0 place-items-center rounded-xl bg-foreground p-2.5 shadow-[0_10px_24px_rgba(15,23,42,0.14)]">
                        <Icon className="h-4 w-4 text-background" />
                    </div>
                </div>
                <div className="text-5xl font-extralight tabular-nums tracking-tight text-foreground animate-count-fade">
                    {formatValue
                        ? (loading ? "" : formatValue(value ?? 0))
                        : suffix === "%"
                            ? (Number(value ?? 0).toFixed(2) + suffix)
                            : (animatedValue.toLocaleString() + suffix)}
                </div>
                <p className="text-xs mt-2 text-muted-foreground/60 font-medium tracking-wide uppercase">
                    {formatValue ? "avg length" : "calls"}
                </p>
            </div>
        </div>
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
        <div className="rounded-xl border border-border/40 bg-card/50 hover:bg-accent/30 transition-all duration-200 p-4 space-y-2">
            <div className="flex items-start justify-between gap-2">
                <div className="flex items-start gap-2.5 min-w-0">
                    {item.contact_name ? (
                        <div className="grid size-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-500 to-purple-600 text-[11px] font-semibold text-white">
                            {getInitials(item.contact_name)}
                        </div>
                    ) : (
                        <div className="grid size-8 shrink-0 place-items-center rounded-full bg-muted text-sm font-semibold text-muted-foreground">?</div>
                    )}
                    <div className="min-w-0">
                        <p className="font-medium text-sm truncate text-foreground">
                            {item.contact_name ?? <span className="text-muted-foreground italic">Unknown caller</span>}
                        </p>
                        <p className="text-xs text-muted-foreground/70 mt-0.5">
                            {formatDate(item.call_date)} · {formatTime(item.call_time)}
                            {item.call_duration_seconds ? ` · ${formatDuration(item.call_duration_seconds)}` : ""}
                        </p>
                        {item.booked_appointment_type_name && (
                            <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                                Booked: {item.booked_appointment_type_name}
                            </span>
                        )}
                        {item.phone_reveal_available && (
                            <RevealablePhone
                                callId={item.call_id}
                                masked={item.phone_masked}
                                available={item.phone_reveal_available}
                                className="mt-1 text-xs"
                            />
                        )}
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
                <p className="text-xs text-muted-foreground/60 line-clamp-2 leading-relaxed pl-[18px]">
                    {item.summary}
                </p>
            )}

            {open && (
                <div className="space-y-2 pt-1 pl-[18px]">
                    <Input
                        placeholder="Resolution note (optional)..."
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
                        {resolving ? "Resolving..." : "Mark Resolved"}
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

function TagBar({ tag, label, count, total, pct, colorClass, barColor }: TagBarProps) {
    const [width, setWidth] = useState(0)

    useEffect(() => {
        const id = setTimeout(() => setWidth(pct), 60)
        return () => clearTimeout(id)
    }, [pct])

    const countPct = total > 0 ? Math.round((count / total) * 100) : 0
    const glowClass = TAG_BAR_GLOW[tag] ?? ""

    return (
        <div className="flex items-center gap-3 group/bar">
            <span className={`inline-flex items-center rounded-lg border px-2.5 py-1 text-xs font-medium w-40 shrink-0 truncate ${colorClass} transition-all duration-200`}>
                {label}
            </span>
            <div className="flex-1 bg-muted/50 rounded-full h-2.5 overflow-hidden">
                <div
                    className={`h-2.5 rounded-full transition-all duration-700 ease-out ${barColor} ${glowClass ? `shadow-sm ${glowClass}` : ""}`}
                    style={{ width: `${width}%` }}
                />
            </div>
            <div className="flex items-center gap-1.5 w-16 justify-end shrink-0">
                <span className="text-sm font-semibold tabular-nums text-foreground">{count}</span>
                <span className="text-xs text-muted-foreground/50">({countPct}%)</span>
            </div>
        </div>
    )
}

// ── Dashboard Page ────────────────────────────────────────────────────────────

export default function Dashboard() {
    const { user } = useAuth()
    const { lastEvent } = useSSE()
    const [summary, setSummary] = useState<DashboardSummary | null>(null)
    const [loading, setLoading] = useState(true)
    const [selectedLocationSlug, setSelectedLocationSlug] = useState<string>("all")
    const [locations, setLocations] = useState<{ slug: string; name: string }[]>([])
    const [aggregateMetrics, setAggregateMetrics] = useState<{
        appointments_booked_month: number
        new_patients_month: number
        booking_rate_month: number
        avg_call_duration_seconds: number
    } | null>(null)

    const [range, setRange] = useState<DateRangeValue>(() => lastNDaysRange(7))

    const fetchSummary = useCallback(async () => {
        try {
            const locationSlug = selectedLocationSlug === "all" ? undefined : selectedLocationSlug
            const summaryData = await getDashboardSummary(locationSlug, range)
            setSummary(summaryData)

            // KPI cards are now sourced from /summary for ALL roles. The
            // backend scopes them by extra_conditions (user.location_id
            // for STAFF/LOCATION_ADMIN, the selected slug for
            // INSTITUTION_ADMIN, or institution-wide when no slug is
            // supplied), so a location admin sees real numbers instead
            // of the hardcoded zeroes that were here before.
            setAggregateMetrics({
                appointments_booked_month: summaryData.appointments_booked_month ?? 0,
                new_patients_month: summaryData.new_patients_month ?? 0,
                booking_rate_month: summaryData.booking_rate_month ?? 0,
                avg_call_duration_seconds: summaryData.avg_call_duration_seconds ?? 0,
            })

            // The location switcher list still comes from the aggregate
            // endpoint (institution-admin only — it's the only place
            // that returns clinic_comparison). LOCATION_ADMIN/STAFF
            // can't switch anyway.
            const isInstitutionAdmin = user?.role === "INSTITUTION_ADMIN"
            if (isInstitutionAdmin) {
                try {
                    const aggregateData = await getAggregateDashboard()
                    setLocations(
                        aggregateData.clinic_comparison.map((c) => ({
                            slug: c.location_slug,
                            name: c.location_name,
                        }))
                    )
                } catch {
                    /* keep prior locations on transient failure */
                }
            } else {
                setLocations([])
            }
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load dashboard"
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [selectedLocationSlug, user?.role, range])

    useEffect(() => {
        fetchSummary()
    }, [fetchSummary])

    useEffect(() => {
        if (lastEvent?.type !== "dashboard_updated" && lastEvent?.type !== "calls_updated") {
            return
        }
        fetchSummary()
    }, [fetchSummary, lastEvent])

    const todayStr = new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })

    const callbackQueue = summary?.callback_queue ?? []
    const tagCounts = summary?.tag_counts ?? []
    const hasCallbacks = callbackQueue.length > 0

    const totalTagCount = tagCounts.reduce((sum, tc) => sum + tc.count, 0)

    return (
        <div className="flex-1 min-h-screen bg-background animate-fade-in-up">
            {/* Violet top-right corner glow */}
            <div className="fixed inset-0 overflow-hidden pointer-events-none">
                <div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" />
            </div>

            <div className="relative z-10 p-8 pt-6 space-y-6">
                {/* Header */}
                <div className="flex items-center justify-between">
                    <div>
                        <h2 className="text-2xl font-bold tracking-tight">Dashboard</h2>
                        <p className="text-sm text-muted-foreground/70 mt-0.5">
                            {todayStr} · Call activity overview.
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        {user?.role === "INSTITUTION_ADMIN" && (
                            <Select value={selectedLocationSlug} onValueChange={setSelectedLocationSlug}>
                                <SelectTrigger className="w-[180px] h-8 text-xs">
                                    <MapPin className="mr-2 h-3.5 w-3.5" />
                                    <SelectValue placeholder="Select location" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All Locations</SelectItem>
                                    {locations.map((loc) => (
                                        <SelectItem key={loc.slug} value={loc.slug}>
                                            {loc.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        )}
                        <DateRangePicker value={range} onChange={setRange} />
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
                </div>

                {/* Range-scoped cards (driven by the date-range picker) */}
                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                    {RANGE_CARD_CONFIG.map(({ label, key, icon, accentColor, glowRgb, suffix }) => (
                        <GlassCard
                            key={key}
                            label={label}
                            value={summary?.range?.[key]}
                            icon={icon}
                            accentColor={accentColor}
                            glowRgb={glowRgb}
                            suffix={suffix}
                            loading={loading}
                        />
                    ))}
                </div>

                {/* Metric cards */}
                {aggregateMetrics && (
                    <div>
                        <div className="flex items-center gap-2 mb-3">
                            <Activity className="h-4 w-4 text-muted-foreground/50" />
                            <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground/50">Monthly Metrics</span>
                        </div>
                        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                            {METRIC_CARDS_CONFIG.map(({ label, key, icon, accentColor, glowRgb }) => (
                                <GlassCard
                                    key={key}
                                    label={label}
                                    value={aggregateMetrics?.[key] ?? 0}
                                    icon={icon}
                                    accentColor={accentColor}
                                    glowRgb={glowRgb}
                                    loading={loading}
                                    suffix={key === "booking_rate_month" ? "%" : ""}
                                    formatValue={key === "avg_call_duration_seconds" ? formatDuration : undefined}
                                />
                            ))}
                        </div>
                    </div>
                )}

                {/* Bottom grid: tag breakdown + callback queue */}
                <div className="grid gap-6 lg:grid-cols-2">
                    {/* Tag breakdown */}
                    <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-card via-card to-accent/30 border border-border/60 shadow-sm">
                        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,hsl(var(--primary)/0.06),transparent_60%)]" />
                        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/20 to-transparent" />
                        <div className="relative">
                            <div className="p-6 pb-4">
                                <h3 className="text-base font-semibold text-foreground">Call Tags Breakdown</h3>
                                <p className="text-sm text-muted-foreground/60 mt-0.5">All-time calls by primary tag.</p>
                            </div>
                            <div className="px-6 pb-6">
                                {loading ? (
                                    <div className="space-y-4">
                                        {Array.from({ length: 5 }).map((_, i) => (
                                            <div key={i} className="flex items-center gap-3">
                                                <Skeleton className="h-5 w-40 rounded-lg" />
                                                <Skeleton className="h-2.5 flex-1 rounded-full" />
                                                <Skeleton className="h-4 w-16" />
                                            </div>
                                        ))}
                                    </div>
                                ) : tagCounts.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center py-10 text-center gap-2">
                                        <div className="h-12 w-12 rounded-2xl bg-primary/10 flex items-center justify-center">
                                            <Phone className="h-6 w-6 text-primary/40" />
                                        </div>
                                        <p className="text-sm font-medium mt-2 text-foreground">No calls recorded yet.</p>
                                        <p className="text-xs text-muted-foreground/50">Tags will appear here once your agent handles calls.</p>
                                    </div>
                                ) : (
                                    <div className="space-y-3.5">
                                        {tagCounts.map((tc) => {
                                            const colorClass = STATUS_COLOR_MAP[tc.tag] ?? "bg-muted text-muted-foreground border-border"
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

                                <div className="mt-5 pt-4 border-t border-border/40">
                                    <Link to="/calls">
                                        <Button variant="ghost" size="sm" className="gap-1.5 text-xs h-7 text-muted-foreground hover:text-foreground">
                                            View all calls <ArrowRight className="h-3 w-3" />
                                        </Button>
                                    </Link>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Callback queue */}
                    <div className={`relative overflow-hidden rounded-2xl bg-gradient-to-br from-card via-card to-accent/30 border shadow-sm transition-all duration-300
                        ${hasCallbacks ? "border-amber-500/20" : "border-border/60"}`}
                    >
                        {hasCallbacks && (
                            <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,rgba(245,158,11,0.05),transparent_60%)]" />
                        )}
                        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/20 to-transparent" />
                        <div className="relative">
                            <div className="p-6 pb-4">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <h3 className="text-base font-semibold text-foreground flex items-center gap-2">
                                            <Clock className={`h-4 w-4 ${hasCallbacks ? "text-amber-500" : "text-muted-foreground/40"}`} />
                                            Needs Callback
                                            {hasCallbacks && (
                                                <Badge
                                                    variant="destructive"
                                                    className="text-[10px] h-5 px-1.5 font-semibold rounded-lg"
                                                >
                                                    {callbackQueue.length}
                                                </Badge>
                                            )}
                                        </h3>
                                        <p className="text-sm text-muted-foreground/60 mt-0.5">Unresolved callback requests, oldest first.</p>
                                    </div>
                                </div>
                            </div>
                            <div className="px-6 pb-6">
                                {loading ? (
                                    <div className="space-y-3">
                                        {Array.from({ length: 3 }).map((_, i) => (
                                            <Skeleton key={i} className="h-16 w-full rounded-xl" />
                                        ))}
                                    </div>
                                ) : callbackQueue.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center py-8 text-center gap-3">
                                        <div className="h-14 w-14 rounded-2xl bg-emerald-500/10 flex items-center justify-center">
                                            <CheckCircle2 className="h-7 w-7 text-emerald-500" />
                                        </div>
                                        <div>
                                            <p className="font-medium text-sm text-foreground">All caught up!</p>
                                            <p className="text-xs text-muted-foreground/50 mt-0.5">No pending callbacks right now.</p>
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
                                    <div className="mt-4 pt-4 border-t border-border/40">
                                        <Link to="/callbacks">
                                            <Button variant="ghost" size="sm" className="gap-1.5 text-xs h-7 text-muted-foreground hover:text-foreground">
                                                <AlertCircle className="h-3 w-3 text-amber-500" />
                                                View all callbacks <ArrowRight className="h-3 w-3" />
                                            </Button>
                                        </Link>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
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
        </div>
    )
}
