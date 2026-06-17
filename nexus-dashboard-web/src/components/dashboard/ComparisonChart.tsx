import { useMemo, useState } from "react"
import { Bar, BarChart, Cell, Label, Pie, PieChart, XAxis, YAxis } from "recharts"
import { Loader2, PieChart as PieIcon } from "lucide-react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
    ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from "@/components/ui/chart"

export interface ComparisonMetricDef {
    key: string
    label: string
    suffix?: string
}

/** A generic entity (location or institution) with its switchable metric values. */
export interface ComparisonRow {
    id: string
    label: string
    values: Record<string, number>
}

const COLORS = [
    "hsl(var(--chart-1))",
    "hsl(var(--chart-2))",
    "hsl(var(--chart-3))",
    "hsl(var(--chart-4))",
    "hsl(var(--chart-5))",
]

// Past this many entities a pie/donut is unreadable — switch to a ranked
// horizontal bar chart (top N) which stays legible at any size.
const DONUT_MAX = 8
const BAR_TOP_N = 12

interface ComparisonChartProps {
    title: string
    rows: ComparisonRow[]
    metrics: ComparisonMetricDef[]
    loading?: boolean
    emptyText?: string
}

/**
 * Metric-switching comparison of entities (locations or institutions). Adapts to
 * scale: a donut for a small set, a ranked top-N horizontal bar chart once there
 * are too many slices to read. Shared by the institution dashboard (locations)
 * and the group dashboard (institutions).
 */
export function ComparisonChart({ title, rows, metrics, loading = false, emptyText = "No data yet." }: ComparisonChartProps) {
    const [activeKey, setActiveKey] = useState<string>(metrics[0]?.key ?? "")
    const activeDef = metrics.find((m) => m.key === activeKey) ?? metrics[0]
    const suffix = activeDef?.suffix ?? ""
    const isRate = suffix === "%"
    const useBars = rows.length > DONUT_MAX

    const ranked = useMemo(() =>
        rows
            .map((row, i) => ({
                label: row.label,
                value: Number(row.values[activeDef?.key ?? ""]) || 0,
                fill: COLORS[i % COLORS.length],
            }))
            .sort((a, b) => b.value - a.value),
    [rows, activeDef])

    const barData = useMemo(() => ranked.slice(0, BAR_TOP_N), [ranked])

    const chartConfig = useMemo<ChartConfig>(() => {
        const cfg: ChartConfig = { value: { label: activeDef?.label ?? "" } }
        ranked.forEach((d, i) => { cfg[d.label] = { label: d.label, color: COLORS[i % COLORS.length] } })
        return cfg
    }, [ranked, activeDef])

    const total = useMemo(() => ranked.reduce((s, d) => s + d.value, 0), [ranked])
    const centerValue = isRate ? Math.round(total / (ranked.length || 1)) : total

    return (
        <Card className="border-border shadow-sm flex-1 flex flex-col">
            <CardHeader className="pb-2">
                <CardTitle className="text-base">{title}</CardTitle>
                <CardDescription>
                    <div className="flex items-center gap-1 flex-wrap mt-1">
                        {metrics.map((m) => (
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
                    <div className="flex items-center justify-center py-12">
                        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    </div>
                ) : !rows.length ? (
                    <div className="flex flex-col items-center justify-center py-12 text-center gap-2">
                        <PieIcon className="h-7 w-7 text-muted-foreground/30" />
                        <p className="text-sm text-muted-foreground">{emptyText}</p>
                    </div>
                ) : useBars ? (
                    <>
                        <ChartContainer config={chartConfig} className="h-[260px] w-full">
                            <BarChart data={barData} layout="vertical" margin={{ left: 8, right: 16 }}>
                                <XAxis type="number" hide />
                                <YAxis
                                    type="category" dataKey="label" width={120}
                                    tickLine={false} axisLine={false}
                                    tick={{ fontSize: 11 }}
                                    tickFormatter={(v: string) => (v.length > 18 ? v.slice(0, 17) + "…" : v)}
                                />
                                <ChartTooltip cursor={false} content={<ChartTooltipContent nameKey="label" hideLabel />} />
                                <Bar dataKey="value" radius={4}>
                                    {barData.map((entry) => <Cell key={entry.label} fill={entry.fill} />)}
                                </Bar>
                            </BarChart>
                        </ChartContainer>
                        {ranked.length > BAR_TOP_N && (
                            <p className="mt-2 text-center text-[11px] text-muted-foreground">
                                Top {BAR_TOP_N} of {ranked.length} — see the table for all.
                            </p>
                        )}
                    </>
                ) : (
                    <div className="flex flex-col items-center gap-6 py-2 sm:flex-row sm:justify-center sm:gap-10">
                        <ChartContainer config={chartConfig} className="aspect-square h-[230px] shrink-0">
                            <PieChart>
                                <ChartTooltip cursor={false} content={<ChartTooltipContent nameKey="label" hideLabel />} />
                                <Pie data={ranked} dataKey="value" nameKey="label" innerRadius={62} outerRadius={95}
                                    paddingAngle={ranked.length > 1 ? 3 : 0} strokeWidth={2}>
                                    {ranked.map((entry) => <Cell key={entry.label} fill={entry.fill} />)}
                                    <Label content={({ viewBox }) => {
                                        if (viewBox && "cx" in viewBox && "cy" in viewBox) {
                                            const cx = viewBox.cx ?? 0
                                            const cy = viewBox.cy ?? 0
                                            return (
                                                <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle">
                                                    <tspan x={cx} y={cy} className="fill-foreground text-2xl font-bold tabular-nums">{centerValue}{suffix}</tspan>
                                                    <tspan x={cx} y={cy + 20} className="fill-muted-foreground text-[11px]">{isRate ? "average" : "total"}</tspan>
                                                </text>
                                            )
                                        }
                                        return null
                                    }} />
                                </Pie>
                            </PieChart>
                        </ChartContainer>
                        <div className="grid w-full max-w-[220px] gap-2.5">
                            {ranked.map((entry) => (
                                <div key={entry.label} className="flex items-center justify-between gap-3 text-sm">
                                    <span className="flex min-w-0 items-center gap-2">
                                        <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: entry.fill }} />
                                        <span className="truncate text-muted-foreground">{entry.label}</span>
                                    </span>
                                    <span className="shrink-0 font-semibold tabular-nums">{entry.value}{suffix}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </CardContent>
        </Card>
    )
}
