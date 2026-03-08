import { useCallback, useEffect, useState } from "react"
import {
    BarChart3,
    Building2,
    DollarSign,
    Loader2,
    MapPin,
    RefreshCcw,
    Settings2,
    TrendingUp,
} from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { getDashboardSummary } from "@/lib/dashboard-api"
import {
    calculateROI,
    getAggregateDashboard,
    getInstitutionPortalMe,
    getLocationOperatingHours,
    getROIConfig,
    listInstitutionPortalLocations,
    updateLocationOperatingHours,
    updateLocationTimezone,
    updateROIConfig,
    type AggregateDashboardResponse,
    type InstitutionPortalLocation,
    type ROICalculation,
    type ROIConfig,
} from "@/lib/institution-portal-api"
import { SUPPORTED_TIMEZONES } from "@/lib/timezones"
import type { DashboardSummary, OperatingHoursEntry } from "@/types"

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
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to save operating hours")
        } finally {
            setSaving(false)
        }
    }

    if (!location) return null

    return (
        <Dialog open={!!location} onOpenChange={(open) => !open && onClose()}>
            <DialogContent className="max-w-2xl">
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
                            <div key={hour.day_of_week} className="flex items-center gap-3 rounded-md border p-3">
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

export default function InstitutionAdminPanel() {
    const [loading, setLoading] = useState(true)
    const [institutionName, setInstitutionName] = useState("")
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [aggregate, setAggregate] = useState<AggregateDashboardResponse | null>(null)
    const [selectedLocation, setSelectedLocation] = useState<InstitutionPortalLocation | null>(null)

    const [drilldownLocationSlug, setDrilldownLocationSlug] = useState("")
    const [drilldownSummary, setDrilldownSummary] = useState<DashboardSummary | null>(null)
    const [drilldownLoading, setDrilldownLoading] = useState(false)
    const [timezoneDraftBySlug, setTimezoneDraftBySlug] = useState<Record<string, string>>({})
    const [timezoneSavingSlug, setTimezoneSavingSlug] = useState<string | null>(null)

    // ROI state
    const [roiConfig, setRoiConfig] = useState<ROIConfig | null>(null)
    const [roiDraft, setRoiDraft] = useState<ROIConfig>({
        avg_appointment_value: 0,
        avg_new_patient_value: 0,
        monthly_subscription_cost: 0,
        staff_hourly_rate: 0,
        avg_call_duration_minutes: 0,
    })
    const [roiSaving, setRoiSaving] = useState(false)
    const [roiCalculation, setRoiCalculation] = useState<ROICalculation | null>(null)
    const [roiCalculating, setRoiCalculating] = useState(false)

    const loadData = useCallback(async () => {
        setLoading(true)
        try {
            const [me, locationRows, aggregateData] = await Promise.all([
                getInstitutionPortalMe(),
                listInstitutionPortalLocations(),
                getAggregateDashboard(),
            ])

            setInstitutionName(me.name)
            setLocations(locationRows)
            setAggregate(aggregateData)

            if (!drilldownLocationSlug) {
                const defaultSlug = aggregateData.clinic_comparison[0]?.location_slug ?? locationRows[0]?.slug ?? ""
                setDrilldownLocationSlug(defaultSlug)
            }

            // Load ROI config
            const existingConfig = await getROIConfig()
            if (existingConfig) {
                setRoiConfig(existingConfig)
                setRoiDraft(existingConfig)
                // Auto-calculate if config exists
                try {
                    const calc = await calculateROI()
                    setRoiCalculation(calc)
                } catch { /* config exists but no calls yet — fine */ }
            }
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to load institution admin panel")
        } finally {
            setLoading(false)
        }
    }, [drilldownLocationSlug])

    useEffect(() => {
        void loadData()
    }, [loadData])

    useEffect(() => {
        async function fetchDrilldown() {
            if (!drilldownLocationSlug) {
                setDrilldownSummary(null)
                return
            }
            setDrilldownLoading(true)
            try {
                const data = await getDashboardSummary(drilldownLocationSlug)
                setDrilldownSummary(data)
            } catch (error: any) {
                toast.error(error?.response?.data?.detail || "Failed to load location drill-down")
            } finally {
                setDrilldownLoading(false)
            }
        }

        void fetchDrilldown()
    }, [drilldownLocationSlug])

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
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to update timezone")
        } finally {
            setTimezoneSavingSlug(null)
        }
    }

    async function handleSaveROIConfig() {
        setRoiSaving(true)
        try {
            const saved = await updateROIConfig(roiDraft)
            setRoiConfig(saved)
            toast.success("ROI configuration saved")
            // Auto-calculate after saving
            setRoiCalculating(true)
            try {
                const calc = await calculateROI()
                setRoiCalculation(calc)
            } catch { /* no calls yet */ }
            setRoiCalculating(false)
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to save ROI configuration")
        } finally {
            setRoiSaving(false)
        }
    }

    async function handleRecalculateROI() {
        if (!roiConfig) return
        setRoiCalculating(true)
        try {
            const calc = await calculateROI()
            setRoiCalculation(calc)
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to calculate ROI")
        } finally {
            setRoiCalculating(false)
        }
    }

    const summary = aggregate?.summary
    const comparisonRows = aggregate?.clinic_comparison ?? []
    const tagDistribution = aggregate?.tag_distribution ?? []

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Institution Admin Panel</h1>
                    <p className="mt-1 text-muted-foreground">
                        Aggregate performance across all locations with location-level operations controls.
                    </p>
                </div>
                <Button variant="outline" onClick={loadData} disabled={loading}>
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCcw className="mr-2 h-4 w-4" />}
                    Refresh
                </Button>
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>Institution</CardDescription>
                        <CardTitle className="text-lg">{institutionName || "—"}</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                        <Building2 className="mr-2 inline h-4 w-4" />
                        Centralized admin scope
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>Total Calls (Month)</CardDescription>
                        <CardTitle className="text-3xl">{summary?.total_calls_month ?? 0}</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                        <BarChart3 className="mr-2 inline h-4 w-4" />
                        Cross-location volume
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>Bookings (Month)</CardDescription>
                        <CardTitle className="text-3xl">{summary?.appointments_booked_month ?? 0}</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                        Booking rate: {summary?.booking_rate_month ?? 0}%
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>Open Callbacks</CardDescription>
                        <CardTitle className="text-3xl">{summary?.open_callbacks ?? 0}</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                        New patients this month: {summary?.new_patients_month ?? 0}
                    </CardContent>
                </Card>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                    <CardHeader>
                        <CardTitle>Tag Distribution</CardTitle>
                        <CardDescription>
                            Primary call outcomes across your institution.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {tagDistribution.map((item) => (
                            <div key={item.tag} className="flex items-center justify-between rounded-md border p-2">
                                <span className="text-sm">{item.label}</span>
                                <span className="text-sm font-semibold">{item.count}</span>
                            </div>
                        ))}
                        {!tagDistribution.length && (
                            <p className="text-sm text-muted-foreground">No call tags available yet.</p>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Location Drill-Down</CardTitle>
                        <CardDescription>
                            Switch to a single location view for detailed operational metrics.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <Label>Location</Label>
                        <Select
                            value={drilldownLocationSlug || undefined}
                            onValueChange={setDrilldownLocationSlug}
                        >
                            <SelectTrigger>
                                <SelectValue placeholder="Select location" />
                            </SelectTrigger>
                            <SelectContent>
                                {locations.map((location) => (
                                    <SelectItem key={location.id} value={location.slug}>
                                        {location.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>

                        {drilldownLoading ? (
                            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                Loading location metrics...
                            </div>
                        ) : drilldownSummary ? (
                            <div className="grid grid-cols-2 gap-2">
                                <div className="rounded-md border p-2">
                                    <p className="text-xs text-muted-foreground">Calls Today</p>
                                    <p className="text-lg font-semibold">{drilldownSummary.call_volume.today}</p>
                                </div>
                                <div className="rounded-md border p-2">
                                    <p className="text-xs text-muted-foreground">This Week</p>
                                    <p className="text-lg font-semibold">{drilldownSummary.call_volume.this_week}</p>
                                </div>
                                <div className="rounded-md border p-2">
                                    <p className="text-xs text-muted-foreground">This Month</p>
                                    <p className="text-lg font-semibold">{drilldownSummary.call_volume.this_month}</p>
                                </div>
                                <div className="rounded-md border p-2">
                                    <p className="text-xs text-muted-foreground">Open Callbacks</p>
                                    <p className="text-lg font-semibold">{drilldownSummary.callback_queue.length}</p>
                                </div>
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground">Select a location to view details.</p>
                        )}
                    </CardContent>
                </Card>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Clinic Comparison</CardTitle>
                    <CardDescription>
                        Side-by-side location performance across call and booking metrics.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Location</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead>Calls Today</TableHead>
                                <TableHead>Calls Month</TableHead>
                                <TableHead>Bookings Month</TableHead>
                                <TableHead>New Patients</TableHead>
                                <TableHead>Booking Rate</TableHead>
                                <TableHead>Open Callbacks</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {comparisonRows.map((row) => (
                                <TableRow key={row.location_id}>
                                    <TableCell className="font-medium">{row.location_name}</TableCell>
                                    <TableCell>{row.status}</TableCell>
                                    <TableCell>{row.calls_today}</TableCell>
                                    <TableCell>{row.calls_this_month}</TableCell>
                                    <TableCell>{row.appointments_booked_month}</TableCell>
                                    <TableCell>{row.new_patients_month}</TableCell>
                                    <TableCell>{row.booking_rate_month}%</TableCell>
                                    <TableCell>{row.open_callbacks}</TableCell>
                                    <TableCell className="text-right">
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() => setDrilldownLocationSlug(row.location_slug)}
                                        >
                                            Drill-down
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {!comparisonRows.length && (
                                <TableRow>
                                    <TableCell colSpan={9} className="py-10 text-center text-muted-foreground">
                                        No location comparison data yet.
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            {/* ROI Configuration & Results */}
            <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <DollarSign className="h-4 w-4" />
                            ROI Configuration
                        </CardTitle>
                        <CardDescription>
                            Enter your practice financials to calculate the return on investment from AI call handling.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-3 sm:grid-cols-2">
                            <div className="space-y-1">
                                <Label htmlFor="roi-appt-value">Avg Appointment Value ($)</Label>
                                <Input
                                    id="roi-appt-value"
                                    type="number"
                                    min={0}
                                    step={1}
                                    value={roiDraft.avg_appointment_value || ""}
                                    onChange={(e) => setRoiDraft((d) => ({ ...d, avg_appointment_value: Number(e.target.value) }))}
                                    placeholder="e.g. 200"
                                />
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="roi-new-patient-value">Avg New Patient Value ($)</Label>
                                <Input
                                    id="roi-new-patient-value"
                                    type="number"
                                    min={0}
                                    step={1}
                                    value={roiDraft.avg_new_patient_value || ""}
                                    onChange={(e) => setRoiDraft((d) => ({ ...d, avg_new_patient_value: Number(e.target.value) }))}
                                    placeholder="e.g. 300"
                                />
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="roi-subscription">Monthly Subscription ($)</Label>
                                <Input
                                    id="roi-subscription"
                                    type="number"
                                    min={0}
                                    step={1}
                                    value={roiDraft.monthly_subscription_cost || ""}
                                    onChange={(e) => setRoiDraft((d) => ({ ...d, monthly_subscription_cost: Number(e.target.value) }))}
                                    placeholder="e.g. 500"
                                />
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="roi-staff-rate">Staff Hourly Rate ($)</Label>
                                <Input
                                    id="roi-staff-rate"
                                    type="number"
                                    min={0}
                                    step={0.5}
                                    value={roiDraft.staff_hourly_rate || ""}
                                    onChange={(e) => setRoiDraft((d) => ({ ...d, staff_hourly_rate: Number(e.target.value) }))}
                                    placeholder="e.g. 18"
                                />
                            </div>
                            <div className="space-y-1 sm:col-span-2">
                                <Label htmlFor="roi-call-duration">Avg Manual Call Duration (min)</Label>
                                <Input
                                    id="roi-call-duration"
                                    type="number"
                                    min={0}
                                    step={0.5}
                                    value={roiDraft.avg_call_duration_minutes || ""}
                                    onChange={(e) => setRoiDraft((d) => ({ ...d, avg_call_duration_minutes: Number(e.target.value) }))}
                                    placeholder="e.g. 4"
                                />
                            </div>
                        </div>
                        <Button onClick={handleSaveROIConfig} disabled={roiSaving}>
                            {roiSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <DollarSign className="mr-2 h-4 w-4" />}
                            Save Configuration
                        </Button>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <TrendingUp className="h-4 w-4" />
                            ROI Summary (This Month)
                        </CardTitle>
                        <CardDescription>
                            {roiConfig ? "Calculated from your call data and configured values." : "Save your ROI configuration to see results."}
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        {!roiConfig ? (
                            <p className="text-sm text-muted-foreground py-8 text-center">
                                Configure your practice financials to calculate ROI.
                            </p>
                        ) : roiCalculating ? (
                            <div className="flex items-center justify-center py-8">
                                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                            </div>
                        ) : roiCalculation ? (
                            <div className="space-y-4">
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="rounded-md border p-3">
                                        <p className="text-xs text-muted-foreground">Calls Handled</p>
                                        <p className="text-2xl font-bold">{roiCalculation.total_calls_month}</p>
                                    </div>
                                    <div className="rounded-md border p-3">
                                        <p className="text-xs text-muted-foreground">Appointments Booked</p>
                                        <p className="text-2xl font-bold">{roiCalculation.appointments_booked_month}</p>
                                    </div>
                                    <div className="rounded-md border p-3">
                                        <p className="text-xs text-muted-foreground">Revenue from Bookings</p>
                                        <p className="text-lg font-semibold text-green-600 dark:text-green-400">
                                            ${roiCalculation.revenue_from_bookings.toLocaleString()}
                                        </p>
                                    </div>
                                    <div className="rounded-md border p-3">
                                        <p className="text-xs text-muted-foreground">New Patient Revenue</p>
                                        <p className="text-lg font-semibold text-green-600 dark:text-green-400">
                                            ${roiCalculation.revenue_from_new_patients.toLocaleString()}
                                        </p>
                                    </div>
                                    <div className="rounded-md border p-3">
                                        <p className="text-xs text-muted-foreground">Staff Time Saved</p>
                                        <p className="text-lg font-semibold">{roiCalculation.staff_time_saved_hours}h</p>
                                        <p className="text-xs text-muted-foreground">${roiCalculation.staff_cost_saved.toLocaleString()} saved</p>
                                    </div>
                                    <div className="rounded-md border p-3">
                                        <p className="text-xs text-muted-foreground">Subscription Cost</p>
                                        <p className="text-lg font-semibold text-red-500">${roiCalculation.monthly_cost.toLocaleString()}</p>
                                    </div>
                                </div>
                                <div className="rounded-lg border-2 border-primary/20 bg-primary/5 p-4 text-center">
                                    <p className="text-xs text-muted-foreground uppercase tracking-wider">Monthly ROI</p>
                                    <p className={`text-4xl font-bold ${roiCalculation.roi_percentage >= 0 ? "text-green-600 dark:text-green-400" : "text-red-500"}`}>
                                        {roiCalculation.roi_percentage}%
                                    </p>
                                    <p className="text-sm text-muted-foreground mt-1">
                                        Net value: ${roiCalculation.net_value.toLocaleString()}
                                    </p>
                                </div>
                                <Button variant="outline" size="sm" onClick={handleRecalculateROI} disabled={roiCalculating} className="w-full">
                                    <RefreshCcw className="mr-2 h-4 w-4" />
                                    Recalculate
                                </Button>
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground py-8 text-center">
                                No calculation data available. Try recalculating.
                            </p>
                        )}
                    </CardContent>
                </Card>
            </div>

            <Card>
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
                                        <TableCell>{location.is_active ? "Active" : "Inactive"}</TableCell>
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
                </CardContent>
            </Card>

            <HoursDialog location={selectedLocation} onClose={() => setSelectedLocation(null)} />
        </div>
    )
}
