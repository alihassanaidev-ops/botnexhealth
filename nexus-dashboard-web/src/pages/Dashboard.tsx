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
    Clock,
    MapPin,
    Activity,
    CalendarDays,
    Home,
} from "lucide-react"
import callsArt from "@/assets/icons/presentation/calls.png"
import schedulingArt from "@/assets/icons/presentation/scheduling.png"
import patientsArt from "@/assets/icons/presentation/patients-outlined.png"
import dashboardArt from "@/assets/icons/presentation/dashboard.png"

import { PageHeader } from "@/components/PageHeader"
import {
    UiBadge,
    UiButton,
    UiInput,
    UiSelect,
    UiSkeleton,
} from "@/components/foundation/Primitives"
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
import "@/components/dashboard/dashboard.css"

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
    { label: "Total Calls", key: "total_calls" as const, art: callsArt, caption: "calls handled" },
    { label: "Appointments Booked", key: "appointments_booked" as const, art: schedulingArt, caption: "appointments" },
    { label: "New Patients", key: "new_patients" as const, art: patientsArt, caption: "first-time callers" },
    { label: "Booking Rate", key: "booking_rate" as const, art: dashboardArt, suffix: "%", caption: "of calls booked" },
]

const METRIC_CARDS_CONFIG = [
    {
        label: "Appointments Booked",
        key: "appointments_booked_month" as const,
        art: schedulingArt,
        caption: "appointments",
    },
    {
        label: "New Patients",
        key: "new_patients_month" as const,
        art: patientsArt,
        caption: "first-time callers",
    },
    {
        label: "Booking Rate",
        key: "booking_rate_month" as const,
        art: dashboardArt,
        caption: "of calls booked",
    },
    {
        label: "Avg Call Duration",
        key: "avg_call_duration_seconds" as const,
        art: callsArt,
        caption: "average length",
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
    art: string
    loading: boolean
    suffix?: string
    caption: string
    formatValue?: (val: number) => string
    emphasis?: "primary" | "secondary"
}

function GlassCard({
    label,
    value,
    art,
    loading,
    suffix = "",
    caption,
    formatValue,
    emphasis = "primary",
}: GlassCardProps) {
    const animatedValue = useAnimatedCount(loading ? undefined : (value ?? 0))

    if (loading) {
        return (
            <div className="metric-card" data-emphasis={emphasis}>
                <div className="metric-card-head">
                    <UiSkeleton className="metric-card-label-ghost" />
                    <UiSkeleton className="metric-card-icon" />
                </div>
                <UiSkeleton className="metric-card-value-ghost" />
                <UiSkeleton className="metric-card-meta-ghost" />
            </div>
        )
    }

    return (
        <div className="metric-card" data-emphasis={emphasis}>
            <div className="metric-card-head">
                <span className="metric-card-label">{label}</span>
                <div className="metric-card-icon ui-artwork" aria-hidden="true">
                    <img src={art} alt="" />
                </div>
            </div>
            <div className="metric-card-value animate-count-fade">
                {formatValue
                    ? (loading ? "" : formatValue(value ?? 0))
                    : suffix === "%"
                        ? (Number(value ?? 0).toFixed(1) + suffix)
                        : (animatedValue.toLocaleString() + suffix)}
            </div>
            <p className="metric-card-meta">{caption}</p>
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
        <div className="dashboard-queue-item">
            <div className="flex items-start justify-between gap-2">
                <div className="flex items-start gap-2.5 min-w-0">
                    {item.contact_name ? (
                        <div className="grid size-8 shrink-0 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
                            {getInitials(item.contact_name)}
                        </div>
                    ) : (
                        <div className="grid size-8 shrink-0 place-items-center rounded-full bg-muted text-sm font-semibold text-muted-foreground">?</div>
                    )}
                    <div className="min-w-0">
                        <p className="font-medium text-sm truncate text-foreground">
                            {item.contact_name ?? <span className="text-muted-foreground italic">Unknown caller</span>}
                        </p>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            {formatDate(item.call_date)} · {formatTime(item.call_time)}
                            {item.call_duration_seconds ? ` · ${formatDuration(item.call_duration_seconds)}` : ""}
                        </p>
                        {item.booked_appointment_type_name && (
                            <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-600 dark:text-emerald-400">
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
                <UiButton
                    variant={open ?"quiet" :"secondary"}
                    size="sm"
                    className="text-xs gap-1 shrink-0"
                    onClick={() => setOpen((o) => !o)}
                >
                    {open ? "Cancel" : "Resolve"}
                </UiButton>
            </div>

            {item.summary && (
                <p className="dashboard-queue-indent text-xs text-muted-foreground line-clamp-2 leading-relaxed">
                    {item.summary}
                </p>
            )}

            {open && (
                <div className="dashboard-queue-indent space-y-2 pt-1">
                    <UiInput
                        placeholder="Resolution note (optional)..."
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        className="text-xs h-8"
                    />
                    <UiButton
                        variant="primary"
                        size="sm"
                        className="gap-1.5 w-full"
                        onClick={handleResolve}
                        disabled={resolving}
                    >
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        {resolving ? "Resolving..." : "Mark Resolved"}
                    </UiButton>
                </div>
            )}
        </div>
    )
}

// ── Animated Tag Bar ──────────────────────────────────────────────────────────

interface TagBarProps {
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
        const id = setTimeout(() => setWidth(pct), 60)
        return () => clearTimeout(id)
    }, [pct])

    const countPct = total > 0 ? Math.round((count / total) * 100) : 0

    return (
        <div className="dashboard-tag-row group/bar">
            <span className={`dashboard-tag-label ${colorClass}`}>
                {label}
            </span>
            <div className="dashboard-tag-track">
                <div className={`dashboard-tag-fill ${barColor}`} style={{ width: `${width}%` }} />
            </div>
            <div className="dashboard-tag-count">
                <span className="text-sm font-semibold tabular-nums text-foreground">{count}</span>
                <span className="text-xs text-muted-foreground">({countPct}%)</span>
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
        <div className="ui-page animate-fade-in-up">
            <div className="ui-page-stack">
                <PageHeader
                    icon={Home}
                    title="Dashboard"
                    description={<>{todayStr} · Call activity overview.</>}
                    actions={
                        <>
                            {user?.role === "INSTITUTION_ADMIN" && (
                                <div className="dashboard-location-control">
                                    <MapPin className="pointer-events-none absolute left-2.5 top-1/2 z-10 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                                    <UiSelect
                                        aria-label="Select location"
                                        value={selectedLocationSlug}
                                        onChange={(event) => setSelectedLocationSlug(event.target.value)}
                                        uiSize="sm"
                                        className="pl-8"
                                    >
                                        <option value="all">All Locations</option>
                                        {locations.map((loc) => (
                                            <option key={loc.slug} value={loc.slug}>
                                                {loc.name}
                                            </option>
                                        ))}
                                    </UiSelect>
                                </div>
                            )}
                            <DateRangePicker value={range} onChange={setRange} />
                            <UiButton
                                variant="secondary"
                                size="sm"
                                onClick={fetchSummary}
                                disabled={loading}
                                className="gap-2"
                            >
                                <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                                Refresh
                            </UiButton>
                        </>
                    }
                />

                {/* Range-scoped cards (driven by the date-range picker) */}
                <div>
                    <div className="ui-section-label dashboard-section-label">
                        <CalendarDays className="h-4 w-4" />
                        <span>Selected range</span>
                    </div>
                    <div className="dashboard-metric-grid dashboard-metric-grid-primary">
                        {RANGE_CARD_CONFIG.map(({ label, key, art, suffix, caption }) => (
                            <GlassCard
                                key={key}
                                label={label}
                                value={summary?.range?.[key]}
                                art={art}
                                suffix={suffix}
                                caption={caption}
                                loading={loading}
                            />
                        ))}
                    </div>
                </div>

                {/* Metric cards */}
                {aggregateMetrics && (
                    <div>
                        <div className="ui-section-label dashboard-section-label">
                            <Activity className="h-4 w-4" />
                            <span>This month</span>
                        </div>
                        <div className="dashboard-metric-grid dashboard-metric-grid-secondary">
                            {METRIC_CARDS_CONFIG.map(({ label, key, art, caption }) => (
                                <GlassCard
                                    key={key}
                                    label={label}
                                    value={aggregateMetrics?.[key] ?? 0}
                                    art={art}
                                    loading={loading}
                                    caption={caption}
                                    suffix={key === "booking_rate_month" ? "%" : ""}
                                    formatValue={key === "avg_call_duration_seconds" ? formatDuration : undefined}
                                    emphasis="secondary"
                                />
                            ))}
                        </div>
                    </div>
                )}

                {/* Bottom grid: tag breakdown + callback queue */}
                <div className="dashboard-detail-grid">
                    {/* Tag breakdown */}
                    <div className="dashboard-panel">
                        <div>
                            <div className="dashboard-panel-header">
                                <h3>Call Tags Breakdown</h3>
                                <p>All-time calls by primary tag.</p>
                            </div>
                            <div className="dashboard-panel-body">
                                {loading ? (
                                    <div className="space-y-4">
                                        {Array.from({ length: 5 }).map((_, i) => (
                                            <div key={i} className="flex items-center gap-3">
                                                <UiSkeleton className="h-5 w-40 rounded-lg" />
                                                <UiSkeleton className="h-2.5 flex-1 rounded-full" />
                                                <UiSkeleton className="h-4 w-16" />
                                            </div>
                                        ))}
                                    </div>
                                ) : tagCounts.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center py-10 text-center gap-2">
                                        <div className="h-12 w-12 rounded-2xl bg-primary/10 flex items-center justify-center">
                                            <Phone className="h-6 w-6 text-primary" />
                                        </div>
                                        <p className="text-sm font-medium mt-2 text-foreground">No calls recorded yet.</p>
                                        <p className="text-xs text-muted-foreground">Tags will appear here once your agent handles calls.</p>
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

                                <div className="dashboard-panel-footer">
                                    <Link to="/calls">
                                        <UiButton variant="quiet" size="sm" className="gap-1.5 text-xs">
                                            <PhoneIncoming className="h-3 w-3" /> All calls
                                        </UiButton>
                                    </Link>
                                    <Link to="/calls" state={{ tags: ["appointment_booked"] }}>
                                        <UiButton variant="quiet" size="sm" className="gap-1.5 text-xs">
                                            <PhoneOutgoing className="h-3 w-3" /> Booked today
                                        </UiButton>
                                    </Link>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Callback queue */}
                    <div className="dashboard-panel">
                        <div>
                            <div className="dashboard-panel-header">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <h3 className="flex items-center gap-2">
                                            <Clock className={`h-4 w-4 ${hasCallbacks ? "text-amber-500" : "text-muted-foreground"}`} />
                                            Needs Callback
                                            {hasCallbacks && (
                                                <UiBadge
                                                    tone="danger"
                                                    className="text-2xs h-5 px-1.5 font-semibold rounded-lg"
                                                >
                                                    {callbackQueue.length}
                                                </UiBadge>
                                            )}
                                        </h3>
                                        <p>Unresolved callback requests, oldest first.</p>
                                    </div>
                                </div>
                            </div>
                            <div className="dashboard-panel-body">
                                {loading ? (
                                    <div className="space-y-3">
                                        {Array.from({ length: 3 }).map((_, i) => (
                                            <UiSkeleton key={i} className="h-16 w-full rounded-xl" />
                                        ))}
                                    </div>
                                ) : callbackQueue.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center py-8 text-center gap-3">
                                        <div className="h-14 w-14 rounded-2xl bg-emerald-500/10 flex items-center justify-center">
                                            <CheckCircle2 className="h-7 w-7 text-emerald-500" />
                                        </div>
                                        <div>
                                            <p className="font-medium text-sm text-foreground">All caught up!</p>
                                            <p className="text-xs text-muted-foreground mt-0.5">No pending callbacks right now.</p>
                                        </div>
                                        <Link to="/calls">
                                            <UiButton variant="secondary" size="sm" className="gap-1.5 text-xs mt-1">
                                                View all calls <ArrowRight className="h-3 w-3" />
                                            </UiButton>
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
                                    <div className="dashboard-panel-footer">
                                        <Link to="/callbacks">
                                            <UiButton variant="quiet" size="sm" className="gap-1.5 text-xs">
                                                <AlertCircle className="h-3 w-3 text-amber-500" />
                                                View all callbacks <ArrowRight className="h-3 w-3" />
                                            </UiButton>
                                        </Link>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    )
}
