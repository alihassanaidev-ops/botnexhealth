import { useCallback, useEffect, useMemo, useState } from "react"
import {
    ArrowDown,
    ArrowUp,
    CalendarCheck,
    Clock,
    CreditCard,
    DollarSign,
    Loader2,
    MapPin,
    Phone,
    RefreshCcw,
    Settings2,
    TrendingUp,
    UserPlus,
} from "lucide-react"
import { Cell, Label, Pie, PieChart } from "recharts"
import { DateRangePicker } from "@/components/dashboard/DateRangePicker"
import { lastNDaysRange, type DateRangeValue } from "@/lib/date-range"

import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import {
    calculateROI,
    getAggregateDashboard,
    getLocationOperatingHours,
    getROIConfig,
    listInstitutionPortalLocations,
    updateLocationOperatingHours,
    updateLocationTimezone,
    type AggregateDashboardResponse,
    type ClinicComparisonRow,
    type InstitutionPortalLocation,
    type ROICalculation,
    type ROIConfig,
} from "@/lib/institution-portal-api"
import { SUPPORTED_TIMEZONES } from "@/lib/timezones"
import type { OperatingHoursEntry } from "@/types"

const DAYS = [
    { value: 0, label: "Monday" },
    { value: 1, label: "Tuesday" },
    { value: 2, label: "Wednesday" },
    { value: 3, label: "Thursday" },
    { value: 4, label: "Friday" },
    { value: 5, label: "Saturday" },
    { value: 6, label: "Sunday" },
]

function defaultHours(): OperatingHoursEntry[] {
    return DAYS.map((day) => ({
        day_of_week: day.value,
        is_open: day.value >= 0 && day.value <= 4,
        open_time: day.value >= 0 && day.value <= 4 ? "09:00" : null,
        close_time: day.value >= 0 && day.value <= 4 ? "17:00" : null,
    }))
}

interface HoursDialogProps {
    location: InstitutionPortalLocation | null
    onClose: () => void
}

function HoursDialog({ location, onClose }: HoursDialogProps) {
    const [loading, setLoading] = useState(false)
    const [saving, setSaving] = useState(false)
    const [hours, setHours] = useState<OperatingHoursEntry[]>(defaultHours)

    useEffect(() => {
        if (!location) return
        setLoading(true)
        getLocationOperatingHours(location.slug)
            .then((rows) => {
                if (!rows.length) {
                    setHours(defaultHours())
                    return
                }
                setHours(
                    DAYS.map((day) => {
                        const found = rows.find((h) => h.day_of_week === day.value)
                        if (!found) {
                            return {
                                day_of_week: day.value,
                                is_open: false,
                                open_time: null,
                                close_time: null,
                            }
                        }
                        return {
                            day_of_week: found.day_of_week,
                            is_open: found.is_open,
                            open_time: found.open_time,
                            close_time: found.close_time,
                        }
                    }),
                )
            })
            .catch((error) => {
                toast.error(error?.response?.data?.detail || "Failed to load operating hours")
            })
            .finally(() => setLoading(false))
    }, [location])

    function setHour(day: number, patch: Partial<OperatingHoursEntry>) {
        setHours((prev) => prev.map((h) => (h.day_of_week === day ? { ...h, ...patch } : h)))
    }

    async function saveHours() {
        if (!location) return
        setSaving(true)
        try {
            await updateLocationOperatingHours(
                location.slug,
                hours.map((h) => ({
                    ...h,
                    open_time: h.is_open ? h.open_time : null,
                    close_time: h.is_open ? h.close_time : null,
                })),
            )
            toast.success("Operating hours saved")
            onClose()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to save operating hours")
        } finally {
            setSaving(false)
        }
    }

    if (!location) return null

    return (
        <Dialog open={!!location} onOpenChange={(open) => !open && onClose()}>
            <DialogContent className="max-w-2xl border-border bg-gradient-to-b from-background to-accent/30">
                <DialogHeader>
                    <DialogTitle>Operating Hours: {location.name}</DialogTitle>
                </DialogHeader>
                {loading ? (
                    <div className="flex items-center justify-center py-12">
                        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                ) : (
                    <div className="space-y-3">
                        {hours.map((hour) => (
                            <div key={hour.day_of_week} className="flex items-center gap-3 rounded-lg border border-border/70 bg-muted/20 p-3">
                                <div className="flex w-40 items-center gap-2">
                                    <Switch
                                        checked={hour.is_open}
                                        onCheckedChange={(value) => setHour(hour.day_of_week, { is_open: value })}
                                    />
                                    <span className="text-sm font-medium">
                                        {DAYS.find((d) => d.value === hour.day_of_week)?.label}
                                    </span>
                                </div>
                                <Input
                                    type="time"
                                    className="w-36"
                                    disabled={!hour.is_open}
                                    value={hour.open_time || "09:00"}
                                    onChange={(e) => setHour(hour.day_of_week, { open_time: e.target.value })}
                                />
                                <span className="text-muted-foreground">to</span>
                                <Input
                                    type="time"
                                    className="w-36"
                                    disabled={!hour.is_open}
                                    value={hour.close_time || "17:00"}
                                    onChange={(e) => setHour(hour.day_of_week, { close_time: e.target.value })}
                                />
                                {!hour.is_open && <span className="text-xs text-muted-foreground">Closed</span>}
                            </div>
                        ))}
                        <div className="flex justify-end pt-2">
                            <Button onClick={saveHours} disabled={saving}>
                                {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Save Hours
                            </Button>
                        </div>
                    </div>
                )}
            </DialogContent>
        </Dialog>
    )
}

// ── Metric options for the comparison chart ──────────────────────────────────

const COMPARISON_METRICS = [
    { key: "booking_rate_month" as const, label: "Booking Rate", suffix: "%" },
    { key: "calls_today" as const, label: "Calls Today", suffix: "" },
    { key: "calls_this_month" as const, label: "Calls (Month)", suffix: "" },
    { key: "appointments_booked_month" as const, label: "Bookings", suffix: "" },
    { key: "new_patients_month" as const, label: "New Patients", suffix: "" },
    { key: "open_callbacks" as const, label: "Callbacks", suffix: "" },
]

type ComparisonMetricKey = (typeof COMPARISON_METRICS)[number]["key"]

const COMPARISON_COLORS = [
    "hsl(var(--chart-1))",
    "hsl(var(--chart-2))",
    "hsl(var(--chart-3))",
    "hsl(var(--chart-4))",
    "hsl(var(--chart-5))",
]

function ClinicComparisonChart({ rows, loading }: { rows: ClinicComparisonRow[]; loading: boolean }) {
    const [activeMetric, setActiveMetric] = useState<ComparisonMetricKey>("booking_rate_month")
    const activeDef = COMPARISON_METRICS.find((m) => m.key === activeMetric)!

    const chartData = useMemo(() =>
        rows.map((row, i) => ({
            location: row.location_name,
            value: Number(row[activeMetric]) || 0,
            fill: COMPARISON_COLORS[i % COMPARISON_COLORS.length],
        })),
    [rows, activeMetric])

    const chartConfig = useMemo<ChartConfig>(() => {
        const cfg: ChartConfig = { value: { label: activeDef.label } }
        rows.forEach((row, i) => {
            cfg[row.location_name] = {
                label: row.location_name,
                color: COMPARISON_COLORS[i % COMPARISON_COLORS.length],
            }
        })
        return cfg
    }, [rows, activeDef.label])

    const isRate = activeDef.suffix === "%"
    const total = useMemo(() => chartData.reduce((s, d) => s + d.value, 0), [chartData])
    const centerValue = isRate
        ? Math.round(total / (chartData.length || 1))
        : total

    return (
        <Card className="border-border shadow-sm flex-1 flex flex-col">
            <CardHeader className="pb-2">
                <CardTitle className="text-base">Clinic Comparison</CardTitle>
                <CardDescription>
                    <div className="flex items-center gap-1 flex-wrap mt-1">
                        {COMPARISON_METRICS.map((m) => (
                            <button
                                key={m.key}
                                onClick={() => setActiveMetric(m.key)}
                                className={`px-2 py-0.5 rounded-md text-[11px] font-medium transition-all duration-150
                                    ${activeMetric === m.key
                                        ? "bg-primary text-primary-foreground shadow-sm"
                                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
                                    }`}
                            >
                                {m.label}
                            </button>
                        ))}
                    </div>
                </CardDescription>
            </CardHeader>
            <CardContent className="flex-1 flex flex-col justify-center">
                {loading ? (
                    <div className="flex items-center justify-center py-12">
                        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    </div>
                ) : !rows.length ? (
                    <div className="flex flex-col items-center justify-center py-12 text-center gap-2">
                        <MapPin className="h-7 w-7 text-muted-foreground/30" />
                        <p className="text-sm text-muted-foreground">No location data yet.</p>
                    </div>
                ) : (
                    <div className="flex flex-col items-center gap-6 py-2 sm:flex-row sm:justify-center sm:gap-10">
                        <ChartContainer config={chartConfig} className="aspect-square h-[230px] shrink-0">
                            <PieChart>
                                <ChartTooltip
                                    cursor={false}
                                    content={<ChartTooltipContent nameKey="location" hideLabel />}
                                />
                                <Pie
                                    data={chartData}
                                    dataKey="value"
                                    nameKey="location"
                                    innerRadius={62}
                                    outerRadius={95}
                                    paddingAngle={chartData.length > 1 ? 3 : 0}
                                    strokeWidth={2}
                                >
                                    {chartData.map((entry) => (
                                        <Cell key={entry.location} fill={entry.fill} />
                                    ))}
                                    <Label
                                        content={({ viewBox }) => {
                                            if (viewBox && "cx" in viewBox && "cy" in viewBox) {
                                                const cx = viewBox.cx ?? 0
                                                const cy = viewBox.cy ?? 0
                                                return (
                                                    <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle">
                                                        <tspan x={cx} y={cy} className="fill-foreground text-2xl font-bold tabular-nums">
                                                            {centerValue}{activeDef.suffix}
                                                        </tspan>
                                                        <tspan x={cx} y={cy + 20} className="fill-muted-foreground text-[11px]">
                                                            {isRate ? "average" : "total"}
                                                        </tspan>
                                                    </text>
                                                )
                                            }
                                            return null
                                        }}
                                    />
                                </Pie>
                            </PieChart>
                        </ChartContainer>

                        {/* Value legend */}
                        <div className="grid w-full max-w-[220px] gap-2.5">
                            {chartData.map((entry) => (
                                <div key={entry.location} className="flex items-center justify-between gap-3 text-sm">
                                    <span className="flex min-w-0 items-center gap-2">
                                        <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: entry.fill }} />
                                        <span className="truncate text-muted-foreground">{entry.location}</span>
                                    </span>
                                    <span className="shrink-0 font-semibold tabular-nums">
                                        {entry.value}{activeDef.suffix}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </CardContent>
        </Card>
    )
}

export default function InstitutionAdminPanel() {
    const [loading, setLoading] = useState(true)
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [aggregate, setAggregate] = useState<AggregateDashboardResponse | null>(null)
    const [selectedLocation, setSelectedLocation] = useState<InstitutionPortalLocation | null>(null)

    const [timezoneDraftBySlug, setTimezoneDraftBySlug] = useState<Record<string, string>>({})
    const [timezoneSavingSlug, setTimezoneSavingSlug] = useState<string | null>(null)

    const [roiConfig, setRoiConfig] = useState<ROIConfig | null>(null)
    const [roiCalculation, setRoiCalculation] = useState<ROICalculation | null>(null)
    const [roiLoading, setRoiLoading] = useState(false)

    const [range, setRange] = useState<DateRangeValue>(() => lastNDaysRange(30))

    const loadData = useCallback(async () => {
        setLoading(true)
        try {
            const [locationRows, aggregateData] = await Promise.all([
                listInstitutionPortalLocations(),
                getAggregateDashboard(range),
            ])

            setLocations(locationRows)
            setAggregate(aggregateData)
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to load institution admin panel")
        } finally {
            setLoading(false)
        }
    }, [range])

    const fetchROI = useCallback(async () => {
        setRoiLoading(true)
        try {
            const config = await getROIConfig()
            if (config) {
                setRoiConfig(config)
                const calc = await calculateROI()
                setRoiCalculation(calc)
            }
        } catch {
            // Silently fail - ROI is optional
        } finally {
            setRoiLoading(false)
        }
    }, [])

    useEffect(() => {
        void loadData()
    }, [loadData])

    useEffect(() => {
        void fetchROI()
    }, [fetchROI])

    useEffect(() => {
        setTimezoneDraftBySlug((prev) => {
            const next: Record<string, string> = {}
            for (const location of locations) {
                next[location.slug] = prev[location.slug] ?? location.timezone ?? "UTC"
            }
            return next
        })
    }, [locations])

    async function handleSaveTimezone(location: InstitutionPortalLocation) {
        const selectedTimezone = timezoneDraftBySlug[location.slug] ?? location.timezone ?? "UTC"
        const currentTimezone = location.timezone ?? "UTC"
        if (selectedTimezone === currentTimezone) return

        setTimezoneSavingSlug(location.slug)
        try {
            const updated = await updateLocationTimezone(location.slug, selectedTimezone)
            setLocations((prev) =>
                prev.map((row) =>
                    row.slug === location.slug
                        ? {
                            ...row,
                            timezone: updated.timezone,
                        }
                        : row,
                ),
            )
            toast.success(`Timezone updated for ${location.name}`)
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to update timezone")
        } finally {
            setTimezoneSavingSlug(null)
        }
    }

    const comparisonRows = aggregate?.clinic_comparison ?? []

    return (
        <div className="relative space-y-6 bg-background">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Institution Admin Panel</h1>
                    <p className="mt-1 text-muted-foreground">
                        Aggregate performance across all locations with location-level operations controls.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <DateRangePicker value={range} onChange={setRange} />
                    <Button onClick={loadData} disabled={loading}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCcw className="mr-2 h-4 w-4" />}
                        Refresh
                    </Button>
                </div>
            </div>


            {/* Chart + ROI side by side (chart goes full-width when ROI has no data) */}
            <div className={`grid gap-6 items-stretch ${roiConfig && roiCalculation ? "lg:grid-cols-[2fr_3fr]" : ""}`}>
                {/* Clinic Comparison Chart */}
                <ClinicComparisonChart rows={comparisonRows} loading={loading} />

                {/* ROI Summary — only render card when config exists */}
                {roiConfig && (
                    <Card className="border-border shadow-sm">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-base flex items-center gap-2">
                                <TrendingUp className="h-4 w-4 text-primary" />
                                ROI Summary
                            </CardTitle>
                            <CardDescription>This month's return on investment.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            {roiLoading ? (
                                <div className="flex items-center justify-center py-8">
                                    <Loader2 className="h-5 w-5 animate-spin text-primary" />
                                </div>
                            ) : roiCalculation ? (
                                <div className="space-y-4">
                                    {/* ROI hero */}
                                    <div className="flex items-center justify-between rounded-xl border border-border p-4">
                                        <div>
                                            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Monthly ROI</p>
                                            <div className="flex items-center gap-1.5 mt-1">
                                                {roiCalculation.roi_percentage >= 0 ? (
                                                    <ArrowUp className="h-4 w-4 text-emerald-600" />
                                                ) : (
                                                    <ArrowDown className="h-4 w-4 text-rose-600" />
                                                )}
                                                <span className={`text-3xl font-extralight tabular-nums ${roiCalculation.roi_percentage >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
                                                    {roiCalculation.roi_percentage}%
                                                </span>
                                            </div>
                                        </div>
                                        <div className="text-right">
                                            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Net Value</p>
                                            <p className="text-lg font-semibold text-foreground mt-1">${roiCalculation.net_value.toLocaleString()}</p>
                                        </div>
                                    </div>

                                    {/* Metrics grid */}
                                    <div className="grid grid-cols-2 gap-3">
                                        {[
                                            { label: "Calls Handled", value: roiCalculation.total_calls_month, icon: Phone },
                                            { label: "Bookings", value: roiCalculation.appointments_booked_month, icon: CalendarCheck },
                                            { label: "Booking Revenue", value: `$${roiCalculation.revenue_from_bookings.toLocaleString()}`, up: true, icon: DollarSign },
                                            { label: "New Patient Rev.", value: `$${roiCalculation.revenue_from_new_patients.toLocaleString()}`, up: true, icon: UserPlus },
                                            { label: "Staff Saved", value: `${roiCalculation.staff_time_saved_hours}h`, up: true, sub: `$${roiCalculation.staff_cost_saved.toLocaleString()}`, icon: Clock },
                                            { label: "Sub. Cost", value: `$${roiCalculation.monthly_cost.toLocaleString()}`, up: false, icon: CreditCard },
                                        ].map((m) => (
                                            <div key={m.label} className="flex items-center gap-3 rounded-xl border border-border p-3">
                                                <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-foreground shadow-[0_10px_24px_rgba(15,23,42,0.14)]">
                                                    <m.icon className="size-5 text-background" />
                                                </div>
                                                <div className="min-w-0">
                                                    <p className="text-[10px] text-muted-foreground font-medium uppercase tracking-wide">{m.label}</p>
                                                    <div className="flex items-center gap-1 mt-0.5">
                                                        {m.up !== undefined && (
                                                            m.up
                                                                ? <ArrowUp className="h-3 w-3 text-emerald-600" />
                                                                : <ArrowDown className="h-3 w-3 text-rose-600" />
                                                        )}
                                                        <span className="text-sm font-semibold tabular-nums text-foreground">{m.value}</span>
                                                    </div>
                                                    {m.sub && <p className="text-[10px] text-muted-foreground/70 mt-0.5">{m.sub} saved</p>}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            ) : (
                                <p className="py-8 text-center text-sm text-muted-foreground">
                                    No calculation data available.
                                </p>
                            )}
                        </CardContent>
                    </Card>
                )}
            </div>

            <Card className="border-border shadow-sm">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <MapPin className="h-4 w-4" />
                        Locations
                    </CardTitle>
                    <CardDescription>
                        View assigned locations and edit timezone or working hours.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="overflow-hidden rounded-lg border border-border/70 bg-background/60">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Location</TableHead>
                                    <TableHead>Phone</TableHead>
                                    <TableHead>Timezone</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {locations.map((location) => {
                                    const selectedTimezone = timezoneDraftBySlug[location.slug] ?? location.timezone ?? "UTC"
                                    const currentTimezone = location.timezone ?? "UTC"
                                    const timezoneChanged = selectedTimezone !== currentTimezone
                                    const savingTimezone = timezoneSavingSlug === location.slug
                                    return (
                                        <TableRow key={location.id}>
                                            <TableCell className="font-medium">{location.name}</TableCell>
                                            <TableCell>{location.phone || "—"}</TableCell>
                                            <TableCell className="min-w-64">
                                                <Select
                                                    value={selectedTimezone}
                                                    onValueChange={(value) =>
                                                        setTimezoneDraftBySlug((prev) => ({
                                                            ...prev,
                                                            [location.slug]: value,
                                                        }))
                                                    }
                                                >
                                                    <SelectTrigger>
                                                        <SelectValue />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        {SUPPORTED_TIMEZONES.map((tz) => (
                                                            <SelectItem key={tz.value} value={tz.value}>
                                                                {tz.label}
                                                            </SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                            </TableCell>
                                            <TableCell>
                                                <span className={location.is_active
                                                    ? "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset bg-green-50 text-green-700 ring-green-600/20 dark:bg-green-900/20 dark:text-green-400 dark:ring-green-900/10"
                                                    : "inline-flex rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground"}
                                                >
                                                    {location.is_active ? "Active" : "Inactive"}
                                                </span>
                                            </TableCell>
                                            <TableCell className="text-right">
                                                <div className="flex justify-end gap-2">
                                                    <Button
                                                        variant="secondary"
                                                        size="sm"
                                                        disabled={!timezoneChanged || savingTimezone}
                                                        onClick={() => void handleSaveTimezone(location)}
                                                    >
                                                        {savingTimezone ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save TZ"}
                                                    </Button>
                                                    <Button variant="outline" size="sm" onClick={() => setSelectedLocation(location)}>
                                                        <Settings2 className="mr-2 h-4 w-4" />
                                                        Edit Hours
                                                    </Button>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    )
                                })}
                                {!locations.length && (
                                    <TableRow>
                                        <TableCell colSpan={5} className="py-10 text-center text-muted-foreground">
                                            No locations found for this institution.
                                        </TableCell>
                                    </TableRow>
                                )}
                            </TableBody>
                        </Table>
                    </div>
                </CardContent>
            </Card>

            <HoursDialog location={selectedLocation} onClose={() => setSelectedLocation(null)} />
        </div>
    )
}
