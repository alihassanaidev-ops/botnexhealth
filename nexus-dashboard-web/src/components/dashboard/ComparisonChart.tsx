import { useMemo, useState } from "react"
import {
    Bar,
    BarChart,
    Cell,
    Label,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts"
import { PieChart as PieIcon } from "lucide-react"

import { UiSkeleton } from "@/components/foundation/Primitives"

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

    const total = useMemo(() => ranked.reduce((s, d) => s + d.value, 0), [ranked])
    const centerValue = isRate ? Math.round(total / (ranked.length || 1)) : total
    const tooltipStyle = {
        border: "1px solid hsl(var(--border))",
        borderRadius: "0.75rem",
        background: "hsl(var(--popover))",
        color: "hsl(var(--popover-foreground))",
        boxShadow: "0 14px 35px rgba(0, 0, 0, 0.18)",
        fontSize: "0.75rem",
    }

    return (
        <section className="flex flex-1 flex-col rounded-2xl border border-border/80 bg-card shadow-sm">
            <div className="px-6 pb-2 pt-5">
                <h3 className="text-base font-semibold">{title}</h3>
                <div className="mt-2 flex flex-wrap items-center gap-1">
                    {metrics.map((m) => (
                        <button
                            key={m.key}
                            type="button"
                            onClick={() => setActiveKey(m.key)}
                            className={`rounded-md px-2 py-1 text-xs font-medium transition-colors duration-150
                                ${activeKey === m.key
                                    ? "bg-foreground text-background"
                                    : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}
                        >
                            {m.label}
                        </button>
                    ))}
                </div>
            </div>
            <div className="flex flex-1 flex-col justify-center px-6 pb-5 pt-2">
                {loading ? (
                    <div className="flex h-[260px] items-end gap-3 px-4 pb-7" aria-hidden="true">
                        {[42, 68, 51, 82, 60, 74].map((height, index) => (
                            <UiSkeleton key={index} className="flex-1" style={{ height: `${height}%` }} />
                        ))}
                    </div>
                ) : !rows.length ? (
                    <div className="flex flex-col items-center justify-center py-12 text-center gap-2">
                        <PieIcon className="h-7 w-7 text-muted-foreground" />
                        <p className="text-sm text-muted-foreground">{emptyText}</p>
                    </div>
                ) : useBars ? (
                    <>
                        <div className="h-[260px] w-full">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={barData} layout="vertical" margin={{ left: 8, right: 16 }}>
                                    <XAxis type="number" hide />
                                    <YAxis
                                        type="category" dataKey="label" width={120}
                                        tickLine={false} axisLine={false}
                                        tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                                        tickFormatter={(v: string) => (v.length > 18 ? v.slice(0, 17) + "…" : v)}
                                    />
                                    <Tooltip cursor={false} contentStyle={tooltipStyle} />
                                    <Bar dataKey="value" radius={4}>
                                        {barData.map((entry) => <Cell key={entry.label} fill={entry.fill} />)}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                        {ranked.length > BAR_TOP_N && (
                            <p className="mt-2 text-center text-xs text-muted-foreground">
                                Top {BAR_TOP_N} of {ranked.length} — see the table for all.
                            </p>
                        )}
                    </>
                ) : (
                    <div className="flex flex-col items-center gap-6 py-2 sm:flex-row sm:justify-center sm:gap-10">
                        <div className="h-[230px] w-[230px] shrink-0">
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Tooltip cursor={false} contentStyle={tooltipStyle} />
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
                                                        <tspan x={cx} y={cy + 20} className="fill-muted-foreground text-xs">{isRate ? "average" : "total"}</tspan>
                                                    </text>
                                                )
                                            }
                                            return null
                                        }} />
                                    </Pie>
                                </PieChart>
                            </ResponsiveContainer>
                        </div>
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
            </div>
        </section>
    )
}
