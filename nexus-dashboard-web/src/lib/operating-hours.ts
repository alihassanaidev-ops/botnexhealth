import type { OperatingHoursEntry } from "@/types"

/** Fallback window for a day switched on without one. */
export const DEFAULT_OPEN_TIME = "09:00"
export const DEFAULT_CLOSE_TIME = "17:00"

/**
 * Guarantee an open day carries a window.
 *
 * Closing a day clears its times, so re-opening one has to put them back.
 * Without this the day is saved as open with no window — a shape the server now
 * rejects, and which used to save cleanly and then quietly stop operating hours
 * applying to that day at all, so the clinic's own booking link offered slots
 * at 6am.
 *
 * A closed day is returned untouched: its times are meaningless and nulling
 * them is what the save path already does.
 */
export function withOpenWindow(entry: OperatingHoursEntry): OperatingHoursEntry {
    if (!entry.is_open) return entry
    if (entry.open_time && entry.close_time) return entry
    return {
        ...entry,
        open_time: entry.open_time || DEFAULT_OPEN_TIME,
        close_time: entry.close_time || DEFAULT_CLOSE_TIME,
    }
}
