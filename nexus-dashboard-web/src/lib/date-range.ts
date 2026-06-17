import { format, subDays } from "date-fns"

const ISO = "yyyy-MM-dd"

/** Inclusive date range as ISO `yyyy-MM-dd` strings. */
export interface DateRangeValue {
    startDate: string
    endDate: string
}

/** "Last N days" ending today, inclusive (so N=7 spans today and the prior 6 days). */
export function lastNDaysRange(days: number): DateRangeValue {
    const end = new Date()
    return { startDate: format(subDays(end, days - 1), ISO), endDate: format(end, ISO) }
}
