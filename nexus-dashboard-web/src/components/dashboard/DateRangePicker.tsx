import { useState } from "react"
import { format, isSameDay, parseISO, startOfDay, subDays } from "date-fns"
import { CalendarIcon } from "lucide-react"
import type { DateRange } from "react-day-picker"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"
import { lastNDaysRange, type DateRangeValue } from "@/lib/date-range"

const ISO = "yyyy-MM-dd"
const PRESETS = [7, 30, 60, 90] as const

interface DateRangePickerProps {
    value: DateRangeValue
    onChange: (value: DateRangeValue) => void
    className?: string
}

export function DateRangePicker({ value, onChange, className }: DateRangePickerProps) {
    const [open, setOpen] = useState(false)
    const [draft, setDraft] = useState<DateRange | undefined>(undefined)

    const start = parseISO(value.startDate)
    const end = parseISO(value.endDate)
    const today = startOfDay(new Date())

    // Which preset (if any) the current value corresponds to — drives both the
    // trigger label and the highlighted preset button.
    const activePreset = PRESETS.find(
        (days) => isSameDay(end, today) && isSameDay(start, subDays(today, days - 1)),
    )
    const label = activePreset
        ? `Last ${activePreset} days`
        : `${format(start, "MMM d")} – ${format(end, "MMM d, yyyy")}`

    // Sync the calendar draft to the committed value whenever the popover opens,
    // instead of in an effect (which would set state on every value change).
    function handleOpenChange(next: boolean) {
        if (next) setDraft({ from: parseISO(value.startDate), to: parseISO(value.endDate) })
        setOpen(next)
    }

    function applyPreset(days: number) {
        onChange(lastNDaysRange(days))
        setOpen(false)
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
                <Button variant="outline" size="sm" className={cn("h-8 gap-2 text-xs", className)}>
                    <CalendarIcon className="h-3.5 w-3.5" />
                    {label}
                </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="flex w-auto flex-col p-0 sm:flex-row">
                <div className="flex shrink-0 flex-row flex-wrap gap-1 border-b border-border/60 p-2 sm:flex-col sm:border-b-0 sm:border-r">
                    {PRESETS.map((days) => (
                        <Button
                            key={days}
                            variant={activePreset === days ? "secondary" : "ghost"}
                            size="sm"
                            className="h-8 justify-start px-3 text-xs font-medium"
                            onClick={() => applyPreset(days)}
                        >
                            Last {days} days
                        </Button>
                    ))}
                </div>
                <Calendar
                    mode="range"
                    numberOfMonths={2}
                    selected={draft}
                    onSelect={handleSelect}
                    defaultMonth={start}
                    disabled={{ after: today }}
                />
            </PopoverContent>
        </Popover>
    )
}
