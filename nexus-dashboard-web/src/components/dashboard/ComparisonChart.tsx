import { useMemo, useState } from "react"
import { Bar, BarChart, LabelList, XAxis, YAxis } from "recharts"
import { BarChart3 } from "lucide-react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
    ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from "@/components/ui/chart"
import { ChartSkeleton } from "@/components/ui/skeletons"

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

/**
 * One measure across entities is a single series, so every bar is the same hue.
 * Giving each clinic its own colour encoded nothing — the label already says
 * which clinic it is — and it went wrong in two ways worth not repeating: the
 * tooltip took its colour from the sorted position while the mark took its own
 * from the unsorted one, so the two disagreed; and past five clinics the hues
 * cycled and two clinics became the same colour.
 */
const SERIES_FILL = "hsl(var(--chart-1))"

const BAR_TOP_N = 12

interface ComparisonChartProps {
    title: string
    rows: ComparisonRow[]
    metrics: ComparisonMetricDef[]
    loading?: boolean
    emptyText?: string
}

/**
 * Metric-switching comparison of entities (locations or institutions).
 *
 * Ranked horizontal bars, always. This was a donut below eight entities, which
 * was the wrong shape for the question in two ways. Comparing magnitudes is what
 * length is for — angle is measurably harder to read — and, more seriously, half
 * these metrics are rates: a booking rate of 60% at one clinic and 40% at another
 * does not make a whole, so drawing them as slices of one invited a reading that
 * was never true. Bars also stay legible as clinics are added, which is why the
 * component already fell back to them.
 */
export function ComparisonChart({ title, rows, metrics, loading = false, emptyText = "No data yet." }: ComparisonChartProps) {
    const [activeKey, setActiveKey] = useState<string>(metrics[0]?.key ?? "")
    const activeDef = metrics.find((m) => m.key === activeKey) ?? metrics[0]
    const suffix = activeDef?.suffix ?? ""

    const ranked = useMemo(() =>
        rows
            .map((row) => ({
                label: row.label,
                value: Number(row.values[activeDef?.key ?? ""]) || 0,
            }))
            .sort((a, b) => b.value - a.value),
    [rows, activeDef])

    const barData = useMemo(() => ranked.slice(0, BAR_TOP_N), [ranked])

    const chartConfig = useMemo<ChartConfig>(
        () => ({ value: { label: activeDef?.label ?? "", color: SERIES_FILL } }),
        [activeDef],
    )

    // A rate has no meaningful total; a count does. Saying which is which keeps
    // the summary from being read as the other one.
    const isRate = suffix === "%"
    const summary = ranked.length
        ? isRate
            ? `${Math.round(ranked.reduce((s, d) => s + d.value, 0) / ranked.length)}${suffix} average`
            : `${ranked.reduce((s, d) => s + d.value, 0).toLocaleString()}${suffix} total`
        : null

    return (
        <Card className="border-border shadow-sm flex-1 flex flex-col">
            <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-3">
                    <CardTitle className="text-base">{title}</CardTitle>
                    {summary && (
                        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{summary}</span>
                    )}
                </div>
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
                    <ChartSkeleton />
                ) : !rows.length ? (
                    <div className="flex flex-col items-center justify-center py-12 text-center gap-2">
                        <BarChart3 className="h-7 w-7 text-muted-foreground/30" />
                        <p className="text-sm text-muted-foreground">{emptyText}</p>
                    </div>
                ) : (
                    <>
                        <ChartContainer
                            config={chartConfig}
                            className="w-full"
                            style={{ height: Math.max(160, barData.length * 34 + 24) }}
                        >
                            <BarChart data={barData} layout="vertical" margin={{ left: 8, right: 44 }}>
                                <XAxis type="number" hide domain={[0, "dataMax"]} />
                                <YAxis
                                    type="category" dataKey="label" width={128}
                                    tickLine={false} axisLine={false}
                                    tick={{ fontSize: 11 }}
                                    tickFormatter={(v: string) => (v.length > 18 ? v.slice(0, 17) + "…" : v)}
                                />
                                <ChartTooltip cursor={false} content={<ChartTooltipContent nameKey="label" hideLabel />} />
                                <Bar dataKey="value" fill={SERIES_FILL} radius={4} barSize={18}>
                                    {/* The value at the end of each bar: this chart is read for
                                        the number as often as for the ranking, and reading it
                                        off a hidden axis is guesswork. */}
                                    <LabelList
                                        dataKey="value"
                                        position="right"
                                        className="fill-muted-foreground"
                                        fontSize={11}
                                        formatter={(v: number) => `${v.toLocaleString()}${suffix}`}
                                    />
                                </Bar>
                            </BarChart>
                        </ChartContainer>
                        {ranked.length > BAR_TOP_N && (
                            <p className="mt-2 text-center text-[11px] text-muted-foreground">
                                Top {BAR_TOP_N} of {ranked.length} — see the table for all.
                            </p>
                        )}
                    </>
                )}
            </CardContent>
        </Card>
    )
}
