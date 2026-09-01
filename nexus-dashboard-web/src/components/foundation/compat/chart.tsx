import * as React from "react"
import { ResponsiveContainer, Tooltip } from "recharts"

import { cn } from "@/lib/utils"

export type ChartConfig = Record<string, { label?: React.ReactNode; color?: string; icon?: React.ComponentType }>
const ChartContext = React.createContext<ChartConfig>({})

export const ChartContainer = React.forwardRef<HTMLDivElement, React.ComponentProps<"div"> & { config: ChartConfig; children: React.ComponentProps<typeof ResponsiveContainer>["children"] }>(function ChartContainer({ className, children, config, ...props }, ref) {
    return <ChartContext.Provider value={config}><div ref={ref} className={cn("flex aspect-video justify-center text-xs", className)} {...props}><ResponsiveContainer>{children}</ResponsiveContainer></div></ChartContext.Provider>
})

export const ChartTooltip = Tooltip
export const ChartTooltipContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement> & { active?: boolean; payload?: Array<{ dataKey?: string | number; name?: string | number; value?: string | number; color?: string }>; label?: React.ReactNode }>(function ChartTooltipContent({ active, payload, label, className, ...props }, ref) {
    const config = React.useContext(ChartContext)
    if (!active || !payload?.length) return null
    return <div ref={ref} className={cn("grid min-w-32 gap-1.5 rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-xl", className)} {...props}>{label ? <div className="font-semibold">{label}</div> : null}<div className="grid gap-1">{payload.map((item) => { const key = String(item.dataKey ?? item.name ?? "value"); return <div key={key} className="flex items-center justify-between gap-4"><span className="flex items-center gap-1.5 text-muted-foreground"><span className="h-2 w-2 rounded-sm" style={{ backgroundColor: item.color ?? config[key]?.color }} />{config[key]?.label ?? item.name}</span><span className="font-medium tabular-nums">{item.value}</span></div> })}</div></div>
})
