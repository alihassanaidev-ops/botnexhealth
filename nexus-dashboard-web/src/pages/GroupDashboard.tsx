import { useCallback, useEffect, useMemo, useState } from "react"
import {
    Building2, Phone, CalendarCheck, UserPlus, Percent, Clock, RefreshCcw, Layers, MapPin,
} from "lucide-react"
import { Area, AreaChart, CartesianGrid, XAxis } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Search, ArrowUpDown } from "lucide-react"
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
    ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from "@/components/ui/chart"
import { StatsCard } from "@/components/dashboard/StatsCard"
import { ComparisonChart, type ComparisonRow } from "@/components/dashboard/ComparisonChart"
import { DateRangePicker } from "@/components/dashboard/DateRangePicker"
import { lastNDaysRange, type DateRangeValue } from "@/lib/date-range"
import { toast } from "sonner"
import {
    getGroupMe, getGroupDashboard, getGroupInstitutionDashboard,
    type GroupMe, type GroupDashboardResponse, type GroupInstitutionDashboardResponse,
    type GroupTrendPoint,
} from "@/lib/group-api"

const ALL = "__all__"

function formatDuration(seconds: number): string {
    if (!seconds) return "0s"
    const m = Math.floor(seconds / 60)
    const s = Math.round(seconds % 60)
    if (m < 1) return `${s}s`
    return s > 0 ? `${m}m ${s}s` : `${m}m`
}

function initials(name: string): string {
    const parts = name.trim().split(/\s+/)
    return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?"
}

const COMPARISON_METRICS = [
    { key: "booking_rate", label: "Booking Rate", suffix: "%" },
    { key: "total_calls", label: "Calls" },
    { key: "appointments_booked", label: "Bookings" },
    { key: "new_patients", label: "New Patients" },
]

const TREND_CONFIG: ChartConfig = {
    total_calls: { label: "Calls", color: "hsl(var(--chart-1))" },
    appointments_booked: { label: "Bookings", color: "hsl(var(--chart-2))" },
}

/** A row in the comparison table/donut — practice or location. */
interface CompareEntity {
    id: string
    label: string
    total_calls: number
    appointments_booked: number
    new_patients: number
    booking_rate: number
    avg_call_duration_seconds: number
}

export default function GroupDashboard() {
    const [me, setMe] = useState<GroupMe | null>(null)
    const [group, setGroup] = useState<GroupDashboardResponse | null>(null)
    const [inst, setInst] = useState<GroupInstitutionDashboardResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [range, setRange] = useState<DateRangeValue>(() => lastNDaysRange(30))
    const [institutionId, setInstitutionId] = useState<string>(ALL)
    const [locationId, setLocationId] = useState<string>(ALL)
    const [search, setSearch] = useState("")
    const [sortKey, setSortKey] = useState<keyof CompareEntity>("total_calls")
    const [sortDir, setSortDir] = useState<"asc" | "desc">("desc")

    // Load the group profile once (drives the institution selector).
    useEffect(() => {
        getGroupMe().then(setMe).catch(() => setMe(null))
    }, [])

    const fetchDashboard = useCallback(async () => {
        setLoading(true)
        try {
            if (institutionId === ALL) {
                setInst(null)
                setGroup(await getGroupDashboard(range))
            } else {
                setGroup(null)
                setInst(await getGroupInstitutionDashboard(
                    institutionId, range, locationId === ALL ? null : locationId,
                ))
            }
        } catch (e) {
            toast.error(e instanceof Error ? e.message : "Failed to load dashboard")
        } finally {
            setLoading(false)
        }
    }, [institutionId, locationId, range])

    useEffect(() => { fetchDashboard() }, [fetchDashboard])

    const inInstitution = institutionId !== ALL
    const atLocation = inInstitution && locationId !== ALL

    // ── Unified view model (group view OR institution drill-in) ──────────────
    const summary = inInstitution ? inst?.summary : group?.summary
    const trend: GroupTrendPoint[] = (inInstitution ? inst?.trend : group?.trend) ?? []
    const locations = inst?.locations ?? []

    const compareEntities: CompareEntity[] = useMemo(() => {
        if (inInstitution) {
            if (atLocation) return []  // single location → no comparison
            return (inst?.location_comparison ?? []).map((r) => ({
                id: r.location_id, label: r.location_name,
                total_calls: r.total_calls, appointments_booked: r.appointments_booked,
                new_patients: r.new_patients, booking_rate: r.booking_rate,
                avg_call_duration_seconds: r.avg_call_duration_seconds,
            }))
        }
        return (group?.institution_comparison ?? []).map((r) => ({
            id: r.institution_id, label: r.institution_name,
            total_calls: r.total_calls, appointments_booked: r.appointments_booked,
            new_patients: r.new_patients, booking_rate: r.booking_rate,
            avg_call_duration_seconds: r.avg_call_duration_seconds,
        }))
    }, [inInstitution, atLocation, inst, group])

    const compareRows: ComparisonRow[] = useMemo(() =>
        compareEntities.map((e) => ({
            id: e.id,
            label: e.label,
            values: {
                booking_rate: e.booking_rate,
                total_calls: e.total_calls,
                appointments_booked: e.appointments_booked,
                new_patients: e.new_patients,
            },
        })),
    [compareEntities])

    // Search + sort for the table (client-side; fine up to a few dozen rows).
    const displayedRows = useMemo(() => {
        const q = search.trim().toLowerCase()
        const filtered = q
            ? compareEntities.filter((e) => e.label.toLowerCase().includes(q))
            : compareEntities
        const dir = sortDir === "asc" ? 1 : -1
        return [...filtered].sort((a, b) => {
            const av = a[sortKey]
            const bv = b[sortKey]
            if (typeof av === "string" && typeof bv === "string") return av.localeCompare(bv) * dir
            return ((av as number) - (bv as number)) * dir
        })
    }, [compareEntities, search, sortKey, sortDir])

    function toggleSort(key: keyof CompareEntity) {
        if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"))
        else { setSortKey(key); setSortDir(key === "label" ? "asc" : "desc") }
    }

    const compareNoun = inInstitution ? "Location" : "Practice"
    const title = inInstitution
        ? (inst?.institution_name ?? "Practice")
        : (me?.name ?? "Group Oversight")

    function onSelectInstitution(v: string) {
        setInstitutionId(v)
        setLocationId(ALL)  // reset location when switching practice
    }

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            {/* Header */}
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight flex items-center gap-2">
                        <Layers className="h-7 w-7" />
                        {title}
                    </h2>
                    <p className="text-muted-foreground mt-1">
                        {inInstitution
                            ? `Practice ${atLocation ? "location " : ""}view — aggregate metrics only, no patient data.`
                            : `Cross-practice performance across ${group?.summary.institution_count ?? me?.members.length ?? 0} practices.`}
                    </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {/* Institution selector */}
                    <Select value={institutionId} onValueChange={onSelectInstitution}>
                        <SelectTrigger className="h-8 w-[200px] text-xs">
                            <SelectValue placeholder="Practice" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value={ALL}>All practices</SelectItem>
                            {(me?.members ?? []).map((m) => (
                                <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    {/* Location selector (only when drilled into a practice) */}
                    {inInstitution && (
                        <Select value={locationId} onValueChange={setLocationId}>
                            <SelectTrigger className="h-8 w-[180px] text-xs">
                                <SelectValue placeholder="Location" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value={ALL}>All locations</SelectItem>
                                {locations.map((l) => (
                                    <SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    )}
                    <DateRangePicker value={range} onChange={setRange} />
                    <Button variant="outline" size="sm" onClick={fetchDashboard} disabled={loading} className="gap-1.5">
                        <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                </div>
            </div>

            {/* KPI cards */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
                {loading || !summary ? (
                    Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-28 w-full rounded-xl" />)
                ) : (
                    <>
                        {!inInstitution && (
                            <StatsCard title="Practices" value={String(group?.summary.institution_count ?? 0)} description="in this group" icon={Building2} tone="primary" />
                        )}
                        {inInstitution && (
                            <StatsCard title="Locations" value={String(locations.length)} description={atLocation ? "(1 selected)" : "in this practice"} icon={MapPin} tone="primary" />
                        )}
                        <StatsCard title="Calls" value={(summary.total_calls ?? 0).toLocaleString()} description="selected range" icon={Phone} />
                        <StatsCard title="Booked" value={(summary.appointments_booked ?? 0).toLocaleString()} description="appointments" icon={CalendarCheck} />
                        <StatsCard title="New patients" value={(summary.new_patients ?? 0).toLocaleString()} description="selected range" icon={UserPlus} />
                        <StatsCard title="Booking rate" value={`${(summary.booking_rate ?? 0).toFixed(1)}%`} description="selected range" icon={Percent} tone="primarySoft" />
                        <StatsCard title="Avg call" value={formatDuration(summary.avg_call_duration_seconds ?? 0)} description="selected range" icon={Clock} />
                    </>
                )}
            </div>

            {/* Trend + comparison */}
            <div className="grid gap-4 lg:grid-cols-2">
                <Card className="border-border shadow-sm">
                    <CardHeader className="pb-2"><CardTitle className="text-base">Call volume trend</CardTitle></CardHeader>
                    <CardContent>
                        {loading ? (
                            <Skeleton className="h-[230px] w-full" />
                        ) : (
                            <ChartContainer config={TREND_CONFIG} className="h-[230px] w-full">
                                <AreaChart data={trend} margin={{ left: 4, right: 8, top: 8 }}>
                                    <defs>
                                        <linearGradient id="grpCalls" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="hsl(var(--chart-1))" stopOpacity={0.35} />
                                            <stop offset="95%" stopColor="hsl(var(--chart-1))" stopOpacity={0.02} />
                                        </linearGradient>
                                        <linearGradient id="grpBookings" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="hsl(var(--chart-2))" stopOpacity={0.3} />
                                            <stop offset="95%" stopColor="hsl(var(--chart-2))" stopOpacity={0.02} />
                                        </linearGradient>
                                    </defs>
                                    <CartesianGrid vertical={false} strokeDasharray="3 3" className="stroke-border/50" />
                                    <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} minTickGap={24} className="text-[10px]" />
                                    <ChartTooltip content={<ChartTooltipContent />} />
                                    <Area type="monotone" dataKey="total_calls" stroke="hsl(var(--chart-1))" fill="url(#grpCalls)" strokeWidth={2} />
                                    <Area type="monotone" dataKey="appointments_booked" stroke="hsl(var(--chart-2))" fill="url(#grpBookings)" strokeWidth={2} />
                                </AreaChart>
                            </ChartContainer>
                        )}
                    </CardContent>
                </Card>

                {atLocation ? (
                    <Card className="border-border shadow-sm flex items-center justify-center">
                        <CardContent className="py-12 text-center text-sm text-muted-foreground">
                            <MapPin className="mx-auto mb-2 h-7 w-7 text-muted-foreground/30" />
                            Viewing a single location. Switch to “All locations” to compare.
                        </CardContent>
                    </Card>
                ) : (
                    <ComparisonChart
                        title={`${compareNoun} comparison`}
                        loading={loading}
                        metrics={COMPARISON_METRICS}
                        rows={compareRows}
                        emptyText={`No ${compareNoun.toLowerCase()} data in this range.`}
                    />
                )}
            </div>

            {/* Comparison table (practices, or locations within a practice) */}
            {!atLocation && (
                <Card>
                    <CardContent className="p-0">
                        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
                            <div className="relative">
                                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                                <Input
                                    placeholder={`Search ${compareNoun.toLowerCase()}…`}
                                    value={search}
                                    onChange={(e) => setSearch(e.target.value)}
                                    className="h-8 w-[200px] pl-8 text-sm"
                                />
                            </div>
                            <span className="text-xs text-muted-foreground">{displayedRows.length} {compareNoun.toLowerCase()}{displayedRows.length === 1 ? "" : "s"}</span>
                        </div>
                        <div className="overflow-x-auto">
                            <Table className="w-full text-sm">
                                <TableHeader className="border-b border-border bg-muted">
                                    <TableRow>
                                        {([
                                            { key: "label", label: compareNoun, align: "left" },
                                            { key: "total_calls", label: "Calls", align: "right" },
                                            { key: "appointments_booked", label: "Booked", align: "right" },
                                            { key: "new_patients", label: "New pts", align: "right" },
                                            { key: "booking_rate", label: "Booking rate", align: "right" },
                                            { key: "avg_call_duration_seconds", label: "Avg call", align: "right" },
                                        ] as const).map((col) => (
                                            <TableHead key={col.key} className={`px-4 py-3 text-${col.align} text-[11px] font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap`}>
                                                <button
                                                    onClick={() => toggleSort(col.key)}
                                                    className={`inline-flex items-center gap-1 hover:text-foreground transition-colors ${col.align === "right" ? "flex-row-reverse" : ""} ${sortKey === col.key ? "text-foreground" : ""}`}
                                                >
                                                    {col.label}
                                                    <ArrowUpDown className={`h-3 w-3 ${sortKey === col.key ? "opacity-100" : "opacity-30"}`} />
                                                </button>
                                            </TableHead>
                                        ))}
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {loading ? (
                                        Array.from({ length: 4 }).map((_, i) => (
                                            <TableRow key={i}>{Array.from({ length: 6 }).map((__, j) => <TableCell key={j} className="px-4 py-3"><Skeleton className="h-4 w-20" /></TableCell>)}</TableRow>
                                        ))
                                    ) : displayedRows.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={6} className="px-4 py-16 text-center">
                                                <div className="flex flex-col items-center gap-3 text-muted-foreground">
                                                    <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center"><Building2 className="h-6 w-6 opacity-40" /></div>
                                                    <p className="font-medium text-sm text-foreground/70">{search ? `No ${compareNoun.toLowerCase()} matches “${search}”` : `No ${compareNoun.toLowerCase()} data in this range`}</p>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        displayedRows.map((row) => (
                                            <TableRow key={row.id} className="hover:bg-muted/60 transition-colors">
                                                <TableCell className="px-4">
                                                    <div className="flex items-center gap-3">
                                                        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-foreground text-background text-xs font-semibold">{initials(row.label)}</span>
                                                        <span className="font-medium">{row.label}</span>
                                                    </div>
                                                </TableCell>
                                                <TableCell className="px-4 text-right tabular-nums font-medium">{row.total_calls.toLocaleString()}</TableCell>
                                                <TableCell className="px-4 text-right tabular-nums">{row.appointments_booked.toLocaleString()}</TableCell>
                                                <TableCell className="px-4 text-right tabular-nums">{row.new_patients.toLocaleString()}</TableCell>
                                                <TableCell className="px-4 text-right tabular-nums">{row.booking_rate.toFixed(1)}%</TableCell>
                                                <TableCell className="px-4 text-right tabular-nums text-muted-foreground">{formatDuration(row.avg_call_duration_seconds)}</TableCell>
                                            </TableRow>
                                        ))
                                    )}
                                </TableBody>
                            </Table>
                        </div>
                    </CardContent>
                </Card>
            )}
        </div>
    )
}
