import { useState } from "react"
import { format, parseISO } from "date-fns"
import { CalendarIcon } from "lucide-react"
import type { DateRange } from "react-day-picker"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"
import {
    allUpcomingRange,
    nextNDaysRange,
    todayISO,
    type UpcomingRange,
} from "@/lib/availability-filter"

/**
 * Forward-looking range picker for the Work Windows list.
 *
 * Deliberately separate from the dashboard's DateRangePicker, which looks
 * *backwards* ("Last 30 days") over closed analytics periods. Scheduling looks
 * forwards, needs an open-ended "all upcoming" default, and clamps the past out
 * unless the operator opts in. Bending one component to do both would have put
 * every analytics page at risk for no gain here.
 */

const ISO = "yyyy-MM-dd"
const PRESETS = [7, 30, 90] as const

interface UpcomingRangePickerProps {
    value: UpcomingRange
    onChange: (value: UpcomingRange) => void
    /** When false, dates before today are unselectable and the range starts today. */
    allowPast?: boolean
    className?: string
}

export function UpcomingRangePicker({
    value,
    onChange,
    allowPast = false,
    className,
}: UpcomingRangePickerProps) {
    const [open, setOpen] = useState(false)
    const [draft, setDraft] = useState<DateRange | undefined>(undefined)

    const today = todayISO()
    // startDate is null when "Include past dates" is on — there is no lower
    // bound, so anchor the calendar on today instead.
    const start = value.startDate ? parseISO(value.startDate) : parseISO(today)
    const end = value.endDate ? parseISO(value.endDate) : undefined

    // Which preset (if any) the current value corresponds to — drives both the
    // trigger label and the highlighted preset button.
    const activePreset = PRESETS.find((days) => {
        const preset = nextNDaysRange(days)
        return value.startDate === preset.startDate && value.endDate === preset.endDate
    })
    const isAllUpcoming = value.startDate === today && value.endDate === null

    const label = isAllUpcoming
        ? "All upcoming"
        : activePreset
            ? `Next ${activePreset} days`
            : value.startDate === null
                ? end
                    ? `Through ${format(end, "MMM d, yyyy")}`
                    : "All dates"
                : end
                    ? `${format(start, "MMM d")} – ${format(end, "MMM d, yyyy")}`
                    : `From ${format(start, "MMM d, yyyy")}`

    // Seed the calendar draft when the popover opens rather than in an effect,
    // which would fire on every value change including the ones we just made.
    function handleOpenChange(next: boolean) {
        if (next) setDraft(value.startDate ? { from: start, to: end } : undefined)
        setOpen(next)
    }

    function handleSelect(range: DateRange | undefined) {
        setDraft(range)
        // Commit once both ends are chosen.
        if (range?.from && range?.to) {
            onChange({ startDate: format(range.from, ISO), endDate: format(range.to, ISO) })
            setOpen(false)
        }
    }

    return (
        <Popover open={open} onOpenChange={handleOpenChange}>
            <PopoverTrigger asChild>
                <Button
                    variant="outline"
                    size="sm"
                    className={cn("h-9 gap-2 text-xs", className)}
                    aria-label="Filter by date range"
                >
                    <CalendarIcon className="h-3.5 w-3.5" />
                    {label}
                </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="flex w-auto flex-col p-0 sm:flex-row">
                <div className="flex shrink-0 flex-row flex-wrap gap-1 border-b border-border/60 p-2 sm:flex-col sm:border-b-0 sm:border-r">
                    <Button
                        variant={isAllUpcoming ? "secondary" : "ghost"}
                        size="sm"
                        className="h-8 justify-start px-3 text-xs font-medium"
                        onClick={() => {
                            onChange(allUpcomingRange())
                            setOpen(false)
                        }}
                    >
                        All upcoming
                    </Button>
                    {PRESETS.map((days) => (
                        <Button
                            key={days}
                            variant={activePreset === days ? "secondary" : "ghost"}
                            size="sm"
                            className="h-8 justify-start px-3 text-xs font-medium"
                            onClick={() => {
                                onChange(nextNDaysRange(days))
                                setOpen(false)
                            }}
                        >
                            Next {days} days
                        </Button>
                    ))}
                </div>
                <Calendar
                    mode="range"
                    numberOfMonths={2}
                    selected={draft}
                    onSelect={handleSelect}
                    defaultMonth={start}
                    disabled={allowPast ? undefined : { before: parseISO(today) }}
                />
            </PopoverContent>
        </Popover>
    )
}
