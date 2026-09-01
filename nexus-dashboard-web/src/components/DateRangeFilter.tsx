import { useState } from "react"
import { format, parseISO, startOfDay, subDays } from "date-fns"
import { CalendarIcon } from "lucide-react"
import type { DateRange } from "react-day-picker"

import { UiButton } from "@/components/foundation/Primitives"
import { Calendar } from "@/components/foundation/compat/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/foundation/compat/popover"
import { cn } from "@/lib/utils"

/**
 * Clearable date-range filter for record lists (Calls, Callback Queue).
 *
 * Unlike the dashboard's DateRangePicker this range is optional — empty means
 * "no date filter" — so it carries a Clear action and an unset label. Both
 * pages previously hand-rolled this with a <details> and two native date
 * inputs; it is one component on react-day-picker now.
 */

const ISO = "yyyy-MM-dd"
const PRESETS = [7, 30, 90] as const

function parseDateString(value: string): Date | undefined {
    if (!value) return undefined
    const parsed = parseISO(value)
    return Number.isNaN(parsed.getTime()) ? undefined : parsed
}

interface DateRangeFilterProps {
    from: string
    to: string
    onChange: (next: { from: string; to: string }) => void
    className?: string
}

export function DateRangeFilter({ from, to, onChange, className }: DateRangeFilterProps) {
    const [open, setOpen] = useState(false)
    const [draft, setDraft] = useState<DateRange | undefined>(undefined)

    const fromDate = parseDateString(from)
    const toDate = parseDateString(to)
    const today = startOfDay(new Date())

    const label = fromDate
        ? toDate
            ? `${format(fromDate, "MMM d, yyyy")} - ${format(toDate, "MMM d, yyyy")}`
            : format(fromDate, "MMM d, yyyy")
        : "Date range"

    function handleOpenChange(next: boolean) {
        if (next) setDraft(fromDate ? { from: fromDate, to: toDate } : undefined)
        setOpen(next)
    }

    function handleSelect(next: DateRange | undefined) {
        setDraft(next)
        if (!next?.from || !next?.to) return
        onChange({ from: format(next.from, ISO), to: format(next.to, ISO) })
        setOpen(false)
    }

    function applyPreset(days: number) {
        onChange({ from: format(subDays(today, days - 1), ISO), to: format(today, ISO) })
        setOpen(false)
    }

    return (
        <Popover open={open} onOpenChange={handleOpenChange}>
            <PopoverTrigger asChild>
                <UiButton
                    type="button"
                    variant="secondary"
                    size="sm"
                    className={cn("gap-2", !fromDate && "text-muted-foreground", className)}
                    aria-label="Filter by date range"
                >
                    <CalendarIcon className="h-3.5 w-3.5" />
                    {label}
                </UiButton>
            </PopoverTrigger>
            <PopoverContent align="start" className="flex w-auto flex-col p-0 sm:flex-row">
                <div className="flex shrink-0 flex-row flex-wrap gap-1 border-b border-border/60 p-2 sm:flex-col sm:border-b-0 sm:border-r">
                    {PRESETS.map((days) => (
                        <UiButton
                            key={days}
                            type="button"
                            variant="quiet"
                            size="sm"
                            className="justify-start px-3 text-xs"
                            onClick={() => applyPreset(days)}
                        >
                            Last {days} days
                        </UiButton>
                    ))}
                    {fromDate && (
                        <UiButton
                            type="button"
                            variant="quiet"
                            size="sm"
                            className="justify-start px-3 text-xs"
                            onClick={() => {
                                setDraft(undefined)
                                onChange({ from: "", to: "" })
                                setOpen(false)
                            }}
                        >
                            Clear
                        </UiButton>
                    )}
                </div>
                <Calendar
                    mode="range"
                    numberOfMonths={2}
                    selected={draft}
                    onSelect={handleSelect}
                    defaultMonth={fromDate ?? subDays(today, 30)}
                    disabled={{ after: today }}
                />
            </PopoverContent>
        </Popover>
    )
}
