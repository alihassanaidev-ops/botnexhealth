import { useMemo, useState } from "react"
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"
import { LineChart as LineIcon, TrendingDown, TrendingUp } from "lucide-react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
    ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from "@/components/ui/chart"
import { ChartSkeleton } from "@/components/ui/skeletons"
import type { MonthlyMetricPoint } from "@/lib/dashboard-api"

/** Single series, so one hue and no legend — the title names what is plotted. */
const SERIES_STROKE = "hsl(var(--chart-1))"

interface TrendMetric {
    key: keyof Pick<
        MonthlyMetricPoint,
        | "total_calls_month"
        | "appointments_booked_month"
        | "new_patients_month"
        | "booking_rate_month"
    >
    label: string
    suffix?: string
}

const METRICS: TrendMetric[] = [
    { key: "total_calls_month", label: "Calls" },
    { key: "appointments_booked_month", label: "Bookings" },
    { key: "new_patients_month", label: "New patients" },
    { key: "booking_rate_month", label: "Booking rate", suffix: "%" },
]

interface TrendChartProps {
    points: MonthlyMetricPoint[]
    loading?: boolean
}

/**
 * One headline metric across whatever window the caller asked for.
 *
 * The KPI tiles answer "how many"; nothing else on the page answers "is that
 * better than before", which is the question an admin acts on. A count on its
 * own cannot be judged — 128 bookings is good or bad only next to the periods
 * either side of it.
 *
 * Buckets are whatever the server chose for the span (daily, weekly or
 * monthly), so this speaks of "periods" rather than months: on the dashboard it
 * follows the date-range picker, on the admin panel it is the last six months.
 */
export function TrendChart({ points, loading = false }: TrendChartProps) {
    const [activeKey, setActiveKey] = useState<TrendMetric["key"]>(METRICS[0].key)
    const active = METRICS.find((m) => m.key === activeKey) ?? METRICS[0]
    const suffix = active.suffix ?? ""

    const data = useMemo(
        () => points.map((p) => ({ label: p.month_label, value: Number(p[active.key]) || 0 })),
        [points, active],
    )

    // Change across the whole window, which is what the chart is being read for.
    // Needs two real buckets; a single point has nothing to be a change from.
    const change = useMemo(() => {
        if (data.length < 2) return null
        const first = data[0].value
        const last = data[data.length - 1].value
        if (first === 0) return null
        return Math.round(((last - first) / first) * 100)
    }, [data])

    const chartConfig = useMemo<ChartConfig>(
        () => ({ value: { label: active.label, color: SERIES_STROKE } }),
        [active],
    )

    return (
        <Card className="border-border shadow-sm flex-1 flex flex-col">
            <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-3">
                    <CardTitle className="text-base">Trend</CardTitle>
                    {change !== null && (
                        <span
                            className={`flex shrink-0 items-center gap-1 text-xs tabular-nums ${
                                change >= 0
                                    ? "text-emerald-700 dark:text-emerald-400"
                                    : "text-amber-700 dark:text-amber-400"
                            }`}
                        >
                            {change >= 0 ? (
                                <TrendingUp className="h-3.5 w-3.5" />
                            ) : (
                                <TrendingDown className="h-3.5 w-3.5" />
                            )}
                            {change >= 0 ? "+" : ""}
                            {change}% across this window
                        </span>
                    )}
                </div>
                <CardDescription>
                    <div className="flex items-center gap-1 flex-wrap mt-1">
                        {METRICS.map((m) => (
                            <button
                                key={m.key}
                                onClick={() => setActiveKey(m.key)}
                                className={`px-2 py-0.5 rounded-md text-[11px] font-medium transition-all duration-150
                                    ${activeKey === m.key
                                        ? "bg-primary text-primary-foreground shadow-sm"
                                        : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}
                            >
                                {m.label}
                            </button>
                        ))}
                    </div>
                </CardDescription>
            </CardHeader>
            <CardContent className="flex-1 flex flex-col justify-center">
                {loading ? (
                    <ChartSkeleton />
                ) : data.length < 2 ? (
                    <div className="flex flex-col items-center justify-center py-12 text-center gap-2">
                        <LineIcon className="h-7 w-7 text-muted-foreground/30" />
                        <p className="text-sm text-muted-foreground">
                            Not enough data in this range — a trend needs at least two points.
                        </p>
                    </div>
                ) : (
                    <ChartContainer config={chartConfig} className="h-[260px] w-full">
                        <LineChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
                            <CartesianGrid vertical={false} strokeDasharray="3 3" className="stroke-border/60" />
                            <XAxis
                                dataKey="label"
                                tickLine={false}
                                axisLine={false}
                                tick={{ fontSize: 11 }}
                                tickMargin={8}
                            />
                            <YAxis
                                tickLine={false}
                                axisLine={false}
                                tick={{ fontSize: 11 }}
                                width={40}
                                tickFormatter={(v: number) => `${v}${suffix}`}
                            />
                            <ChartTooltip content={<ChartTooltipContent />} />
                            <Line
                                dataKey="value"
                                name={active.label}
                                type="monotone"
                                stroke={SERIES_STROKE}
                                strokeWidth={2}
                                dot={{ r: 3, strokeWidth: 0, fill: SERIES_STROKE }}
                                activeDot={{ r: 5 }}
                            />
                        </LineChart>
                    </ChartContainer>
                )}
            </CardContent>
        </Card>
    )
}
