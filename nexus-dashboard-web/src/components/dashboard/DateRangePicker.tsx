import { useState } from "react"
import { format, isSameDay, parseISO, startOfDay, subDays } from "date-fns"
import { CalendarIcon } from "lucide-react"
import type { DateRange } from "react-day-picker"

import { UiButton } from "@/components/foundation/Primitives"
import { Calendar } from "@/components/foundation/compat/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/foundation/compat/popover"
import { cn } from "@/lib/utils"
import { lastNDaysRange, type DateRangeValue } from "@/lib/date-range"

/**
 * Backward-looking analytics range picker (Dashboard, Institution Admin).
 *
 * Built on react-day-picker via the shared Calendar/Popover primitives — the
 * same combination `UpcomingRangePicker` uses — rather than hand-rolled date
 * inputs, so range selection, month paging, and clamping are the library's
 * problem. See `UpcomingRangePicker` for why the forward-looking scheduling
 * picker stays a separate component.
 */

const ISO = "yyyy-MM-dd"
const PRESETS = [7, 30, 60, 90] as const

interface DateRangePickerProps {
    value: DateRangeValue
    onChange: (value: DateRangeValue) => void
    className?: string
}

export function DateRangePicker({ value, onChange, className = "" }: DateRangePickerProps) {
    const [open, setOpen] = useState(false)
    const [draft, setDraft] = useState<DateRange | undefined>(undefined)

    const start = parseISO(value.startDate)
    const end = parseISO(value.endDate)
    const today = startOfDay(new Date())

    const activePreset = PRESETS.find(
        (days) => isSameDay(end, today) && isSameDay(start, subDays(today, days - 1)),
    )
    const label = activePreset
        ? `Last ${activePreset} days`
        : `${format(start, "MMM d")} – ${format(end, "MMM d, yyyy")}`

    function handleOpenChange(next: boolean) {
        // Seed the draft from the committed value each time it opens, so a
        // half-finished selection never leaks into the next visit.
        if (next) setDraft({ from: start, to: end })
        setOpen(next)
    }

    function handleSelect(next: DateRange | undefined) {
        setDraft(next)
        if (!next?.from || !next?.to) return
        onChange({ startDate: format(next.from, ISO), endDate: format(next.to, ISO) })
        setOpen(false)
    }

    function applyPreset(days: number) {
        onChange(lastNDaysRange(days))
        setOpen(false)
    }

    return (
        <Popover open={open} onOpenChange={handleOpenChange}>
            <PopoverTrigger asChild>
                <UiButton
                    type="button"
                    variant="secondary"
                    size="sm"
                    className={cn("gap-2", className)}
                    aria-label="Filter by date range"
                >
                    <CalendarIcon className="h-3.5 w-3.5" />
                    {label}
                </UiButton>
            </PopoverTrigger>
            <PopoverContent align="end" className="flex w-auto flex-col p-0 sm:flex-row">
                <div className="flex shrink-0 flex-row flex-wrap gap-1 border-b border-border/60 p-2 sm:flex-col sm:border-b-0 sm:border-r">
                    {PRESETS.map((days) => (
                        <UiButton
                            key={days}
                            type="button"
                            variant={activePreset === days ? "secondary" : "quiet"}
                            size="sm"
                            className="justify-start px-3 text-xs"
                            onClick={() => applyPreset(days)}
                        >
                            Last {days} days
                        </UiButton>
                    ))}
                </div>
                <Calendar
                    mode="range"
                    numberOfMonths={2}
                    selected={draft}
                    onSelect={handleSelect}
                    defaultMonth={subDays(today, 30)}
                    disabled={{ after: today }}
                />
            </PopoverContent>
        </Popover>
    )
}
