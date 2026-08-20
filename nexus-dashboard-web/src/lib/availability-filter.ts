/**
 * Date logic shared by the Work Windows list (ProvidersScheduling) and the
 * scheduler calendar. Both need the same answer to "does this window apply on
 * this date?", and they used to answer it separately — the calendar inline, the
 * list not at all. Keeping one implementation here stops them drifting.
 *
 * All dates are ISO `yyyy-MM-dd` strings in the *browser's* timezone: the user
 * is sitting at the practice, so their local "today" is the clinic's today.
 * NexHealth returns `days` as long English weekday names ("Monday").
 */

import type { CachedAvailability } from "@/types"

/**
 * Inclusive date window for the list filter. Either bound may be `null`,
 * meaning "unbounded in that direction".
 *
 * The default is `{ startDate: today, endDate: null }` — open-ended forwards,
 * so the list keeps showing every upcoming window until the operator narrows it
 * deliberately, and clamped at today so expired windows stay out. Clearing
 * `startDate` is what "Include past dates" does.
 */
export interface UpcomingRange {
    startDate: string | null
    endDate: string | null
}

/** Today, in the browser's timezone. `en-CA` formats as `yyyy-MM-dd`. */
export function todayISO(): string {
    return new Date().toLocaleDateString("en-CA")
}

/** Shift an ISO date by whole days. The noon anchor keeps DST from shifting the day. */
export function addDays(isoDate: string, delta: number): string {
    const d = new Date(`${isoDate}T12:00:00`)
    d.setDate(d.getDate() + delta)
    return d.toLocaleDateString("en-CA")
}

/** Long weekday name for an ISO date, matching NexHealth's `days` values. */
export function weekdayName(isoDate: string): string {
    return new Date(`${isoDate}T12:00:00`).toLocaleDateString("en-US", { weekday: "long" })
}

/** The default range: open-ended from today. Preserves pre-filter behaviour. */
export function allUpcomingRange(): UpcomingRange {
    return { startDate: todayISO(), endDate: null }
}

/** Is the range unbounded in both directions? Only reachable via "Include past dates". */
export function isUnbounded(range: UpcomingRange): boolean {
    return range.startDate === null && range.endDate === null
}

/** "Next N days" from today, inclusive — N=7 spans today plus the next 6. */
export function nextNDaysRange(days: number): UpcomingRange {
    const start = todayISO()
    return { startDate: start, endDate: addDays(start, days - 1) }
}

/**
 * A window with no `specific_date` is a recurring weekly rule (`days:
 * ["Monday", ...]`) that repeats indefinitely. It has no date to compare, so
 * it belongs to every range — see `matchesRange`.
 */
export function isRecurring(av: CachedAvailability): boolean {
    return !av.specific_date
}

/** A dated window whose date has already passed. Recurring rules never expire. */
export function isExpired(av: CachedAvailability, today: string = todayISO()): boolean {
    return !!av.specific_date && av.specific_date < today
}

/**
 * Does this window fall inside the range?
 *
 * Recurring rules always match: they repeat every week forever, so they are
 * live in any range the user picks. Filtering them out by date would hide the
 * provider's standing schedule the moment a range was applied.
 */
export function matchesRange(av: CachedAvailability, range: UpcomingRange): boolean {
    if (isRecurring(av)) return true
    const date = av.specific_date as string
    if (range.startDate && date < range.startDate) return false
    if (range.endDate && date > range.endDate) return false
    return true
}

/**
 * Does this window apply on one specific date, as something the calendar can
 * draw? Inactive windows and windows missing a usable begin/end time have no
 * band to render, so they never match.
 */
export function matchesDate(av: CachedAvailability, isoDate: string): boolean {
    if (av.active === false) return false
    if (toMinutes(av.begin_time) == null || toMinutes(av.end_time) == null) return false
    if (av.specific_date) return av.specific_date === isoDate
    return (av.days || []).includes(weekdayName(isoDate))
}

/** `"09:30"` → minutes past midnight. `null` for missing or unparseable input. */
export function toMinutes(time?: string | null): number | null {
    if (!time) return null
    const [h, m] = time.split(":").map(Number)
    if (Number.isNaN(h)) return null
    return h * 60 + (m || 0)
}

/**
 * Sort dated windows soonest-first. NexHealth returns rows in insertion order,
 * which reads as random dates to an operator — easy to link an appointment type
 * to a far-future row instead of the soonest one.
 */
export function byDateThenTime(a: CachedAvailability, b: CachedAvailability): number {
    const ad = a.specific_date ?? ""
    const bd = b.specific_date ?? ""
    if (ad !== bd) return ad.localeCompare(bd)
    return (a.begin_time ?? "").localeCompare(b.begin_time ?? "")
}
