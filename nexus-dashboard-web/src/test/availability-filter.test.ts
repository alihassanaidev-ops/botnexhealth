/**
 * Unit tests for the date logic behind the Work Windows filter.
 *
 * The rules that matter and are easy to get wrong:
 *   - a recurring rule (no specific_date) belongs to EVERY range,
 *   - range bounds are inclusive on both ends,
 *   - an open-ended range (endDate: null) never excludes a future date,
 *   - dated rows sort soonest-first, recurring rows never reach the sort.
 */

import { describe, it, expect, vi, afterEach } from "vitest"
import type { CachedAvailability } from "@/types"
import {
    addDays,
    allUpcomingRange,
    byDateThenTime,
    isActive,
    isExpired,
    isRecurring,
    matchesDate,
    matchesRange,
    nextNDaysRange,
    todayISO,
    toMinutes,
    weekdayName,
} from "@/lib/availability-filter"

/** Minimal availability; override only what a given test cares about. */
function av(overrides: Partial<CachedAvailability> = {}): CachedAvailability {
    return {
        id: "a1",
        source_id: "s1",
        provider_source_id: "p1",
        provider_name: "Dr Kadri",
        operatory_source_id: "op1",
        operatory_name: "Room 1",
        begin_time: "09:00",
        end_time: "17:00",
        days: null,
        specific_date: "2026-08-20",
        appointment_type_ids: ["t1"],
        appointment_type_names: ["Cleaning"],
        active: true,
        synced: true,
        source_metadata: null,
        synced_at: null,
        ...overrides,
    }
}

afterEach(() => {
    vi.useRealTimers()
})

describe("todayISO / addDays / weekdayName", () => {
    it("formats today as yyyy-MM-dd", () => {
        expect(todayISO()).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    })

    it("shifts dates forwards and backwards across month boundaries", () => {
        expect(addDays("2026-08-20", 1)).toBe("2026-08-21")
        expect(addDays("2026-08-31", 1)).toBe("2026-09-01")
        expect(addDays("2026-09-01", -1)).toBe("2026-08-31")
        expect(addDays("2026-03-01", -1)).toBe("2026-02-28")
    })

    it("names weekdays the way NexHealth does", () => {
        // 2026-08-20 is a Thursday.
        expect(weekdayName("2026-08-20")).toBe("Thursday")
        expect(weekdayName("2026-08-24")).toBe("Monday")
    })
})

describe("nextNDaysRange", () => {
    it("is inclusive of today, so N=7 spans today plus the next six days", () => {
        const range = nextNDaysRange(7)
        expect(range.startDate).toBe(todayISO())
        expect(range.endDate).toBe(addDays(todayISO(), 6))
    })

    it("N=1 is today only", () => {
        const range = nextNDaysRange(1)
        expect(range.startDate).toBe(range.endDate)
    })
})

describe("allUpcomingRange", () => {
    it("starts today and never closes, so nothing future is excluded", () => {
        const range = allUpcomingRange()
        expect(range.startDate).toBe(todayISO())
        expect(range.endDate).toBeNull()
        expect(matchesRange(av({ specific_date: addDays(todayISO(), 3650) }), range)).toBe(true)
    })

    it("still excludes the past", () => {
        const range = allUpcomingRange()
        expect(matchesRange(av({ specific_date: addDays(todayISO(), -1) }), range)).toBe(false)
    })
})

describe("isRecurring", () => {
    it("is true when there is no specific_date", () => {
        expect(isRecurring(av({ specific_date: null, days: ["Monday"] }))).toBe(true)
    })

    it("is false for a dated window", () => {
        expect(isRecurring(av({ specific_date: "2026-08-20" }))).toBe(false)
    })
})

describe("matchesRange", () => {
    const range = { startDate: "2026-08-10", endDate: "2026-08-20" }

    it("includes both bounds", () => {
        expect(matchesRange(av({ specific_date: "2026-08-10" }), range)).toBe(true)
        expect(matchesRange(av({ specific_date: "2026-08-20" }), range)).toBe(true)
    })

    it("excludes dates either side of the range", () => {
        expect(matchesRange(av({ specific_date: "2026-08-09" }), range)).toBe(false)
        expect(matchesRange(av({ specific_date: "2026-08-21" }), range)).toBe(false)
    })

    it("always includes recurring rules — they repeat forever, so no range can exclude them", () => {
        const recurring = av({ specific_date: null, days: ["Monday"] })
        expect(matchesRange(recurring, range)).toBe(true)
        // Even a range containing no Mondays at all: 2026-08-11 (Tue) → 2026-08-13 (Thu).
        expect(matchesRange(recurring, { startDate: "2026-08-11", endDate: "2026-08-13" })).toBe(true)
        // And a range entirely in the past.
        expect(matchesRange(recurring, { startDate: "2020-01-01", endDate: "2020-01-02" })).toBe(true)
    })

    it("treats a null endDate as open-ended", () => {
        expect(matchesRange(av({ specific_date: "2099-12-31" }), { startDate: "2026-08-10", endDate: null })).toBe(true)
        expect(matchesRange(av({ specific_date: "2026-08-09" }), { startDate: "2026-08-10", endDate: null })).toBe(false)
    })
})

describe("isExpired", () => {
    it("is true only for dates strictly before today", () => {
        expect(isExpired(av({ specific_date: "2026-08-19" }), "2026-08-20")).toBe(true)
        expect(isExpired(av({ specific_date: "2026-08-20" }), "2026-08-20")).toBe(false)
        expect(isExpired(av({ specific_date: "2026-08-21" }), "2026-08-20")).toBe(false)
    })

    it("never expires a recurring rule", () => {
        expect(isExpired(av({ specific_date: null, days: ["Monday"] }), "2026-08-20")).toBe(false)
    })
})

describe("isActive", () => {
    it("treats an explicit false as inactive and everything else as active", () => {
        expect(isActive(av({ active: false }))).toBe(false)
        expect(isActive(av({ active: true }))).toBe(true)
    })

    it("defaults to active when the flag is absent, rather than hiding the row", () => {
        const noFlag = av()
        delete (noFlag as Partial<CachedAvailability>).active
        expect(isActive(noFlag)).toBe(true)
    })
})

describe("matchesDate", () => {
    it("matches a dated window on its own date only", () => {
        expect(matchesDate(av({ specific_date: "2026-08-20" }), "2026-08-20")).toBe(true)
        expect(matchesDate(av({ specific_date: "2026-08-20" }), "2026-08-21")).toBe(false)
    })

    it("matches a recurring window on its weekdays", () => {
        const recurring = av({ specific_date: null, days: ["Monday", "Thursday"] })
        expect(matchesDate(recurring, "2026-08-20")).toBe(true) // Thursday
        expect(matchesDate(recurring, "2026-08-24")).toBe(true) // Monday
        expect(matchesDate(recurring, "2026-08-21")).toBe(false) // Friday
    })

    it("rejects inactive windows and windows with no usable times", () => {
        expect(matchesDate(av({ active: false }), "2026-08-20")).toBe(false)
        expect(matchesDate(av({ begin_time: null }), "2026-08-20")).toBe(false)
        expect(matchesDate(av({ end_time: null }), "2026-08-20")).toBe(false)
    })
})

describe("toMinutes", () => {
    it("converts HH:MM to minutes past midnight", () => {
        expect(toMinutes("00:00")).toBe(0)
        expect(toMinutes("09:30")).toBe(570)
        expect(toMinutes("23:59")).toBe(1439)
    })

    it("returns null rather than NaN for missing or unparseable input", () => {
        expect(toMinutes(null)).toBeNull()
        expect(toMinutes(undefined)).toBeNull()
        expect(toMinutes("")).toBeNull()
        expect(toMinutes("not-a-time")).toBeNull()
    })
})

describe("byDateThenTime", () => {
    it("sorts by date, then by start time within a date", () => {
        const rows = [
            av({ id: "c", specific_date: "2026-08-22", begin_time: "08:00" }),
            av({ id: "b", specific_date: "2026-08-20", begin_time: "14:00" }),
            av({ id: "a", specific_date: "2026-08-20", begin_time: "09:00" }),
        ]
        expect([...rows].sort(byDateThenTime).map((r) => r.id)).toEqual(["a", "b", "c"])
    })
})
