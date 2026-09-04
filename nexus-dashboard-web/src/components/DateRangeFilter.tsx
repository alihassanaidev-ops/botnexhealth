import { useState } from "react"
import { format, startOfDay, subDays } from "date-fns"
import { CalendarIcon } from "lucide-react"
import type { DateRange } from "react-day-picker"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"

/**
 * Clearable date-range filter for record lists (Calls, Callback Queue).
 *
 * Unlike the dashboard's DateRangePicker this range is optional — empty means
 * "no date filter" — so it carries a Clear action and an unset label. Calls
 * and Callbacks each had their own copy of this, identical down to the
 * helpers; this is the shared one, plus the relative presets neither had.
 */

const ISO = "yyyy-MM-dd"
const PRESETS = [7, 30, 90] as const

// Built from the parts rather than `new Date(value)` / `parseISO`, both of
// which can read a bare yyyy-MM-dd as UTC midnight and land the filter on the
// previous day for anyone west of Greenwich.
function parseDateString(value: string): Date | undefined {
    if (!value) return undefined
    const [year, month, day] = value.split("-").map(Number)
    if (!year || !month || !day) return undefined
    const parsed = new Date(year, month - 1, day)
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

    // Held locally until both ends are picked. Committing the first click
    // would fire a fetch for an open-ended range the user is halfway through
    // choosing, and the results would flash before they finish.
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
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className={cn("h-8 justify-start gap-2 text-left font-normal", className)}
                    aria-label="Filter by date range"
                >
                    <CalendarIcon className="h-4 w-4" />
                    <span className={cn(!fromDate && "text-muted-foreground")}>{label}</span>
                </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="flex w-auto flex-col p-0 sm:flex-row">
                <div className="flex shrink-0 flex-row flex-wrap gap-1 border-b border-border/60 p-2 sm:flex-col sm:border-b-0 sm:border-r">
                    {PRESETS.map((days) => (
                        <Button
                            key={days}
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="justify-start px-3 text-xs"
                            onClick={() => applyPreset(days)}
                        >
                            Last {days} days
                        </Button>
                    ))}
                    {fromDate && (
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="justify-start px-3 text-xs"
                            onClick={() => {
                                setDraft(undefined)
                                onChange({ from: "", to: "" })
                                setOpen(false)
                            }}
                        >
                            Clear
                        </Button>
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
