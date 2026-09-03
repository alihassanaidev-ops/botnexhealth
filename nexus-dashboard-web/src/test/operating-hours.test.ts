import { describe, it, expect } from "vitest"
import {
    DEFAULT_CLOSE_TIME,
    DEFAULT_OPEN_TIME,
    withOpenWindow,
} from "@/lib/operating-hours"

const day = (over: Partial<Parameters<typeof withOpenWindow>[0]> = {}) => ({
    day_of_week: 3,
    is_open: true,
    open_time: "09:00" as string | null,
    close_time: "17:00" as string | null,
    ...over,
})

describe("withOpenWindow", () => {
    it("fills the window when a day is switched on without one", () => {
        // The exact sequence that produced the staging defect: toggle a day
        // off (which nulls its times), then toggle it back on.
        const reopened = withOpenWindow(
            day({ is_open: true, open_time: null, close_time: null }),
        )
        expect(reopened.open_time).toBe(DEFAULT_OPEN_TIME)
        expect(reopened.close_time).toBe(DEFAULT_CLOSE_TIME)
    })

    it.each([
        ["open_time", { open_time: null }],
        ["close_time", { close_time: null }],
    ])("fills a half-missing window (%s)", (_label, over) => {
        const filled = withOpenWindow(day(over))
        expect(filled.open_time).toBeTruthy()
        expect(filled.close_time).toBeTruthy()
    })

    it("leaves an already-configured open day alone", () => {
        const entry = day({ open_time: "07:30", close_time: "19:45" })
        expect(withOpenWindow(entry)).toEqual(entry)
    })

    it("leaves a closed day's empty times alone", () => {
        const closed = day({ is_open: false, open_time: null, close_time: null })
        expect(withOpenWindow(closed)).toEqual(closed)
    })

    it("never returns an open day without a window", () => {
        for (const over of [
            { open_time: null, close_time: null },
            { open_time: null },
            { close_time: null },
            {},
        ]) {
            const result = withOpenWindow(day({ ...over, is_open: true }))
            expect(Boolean(result.open_time && result.close_time)).toBe(true)
        }
    })
})
