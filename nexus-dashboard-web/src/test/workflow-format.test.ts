import { describe, expect, it } from "vitest"
import { humanizeSeconds } from "@/lib/workflow/format"

describe("humanizeSeconds", () => {
    it("formats durations", () => {
        expect(humanizeSeconds(3600)).toBe("1 hour")
        expect(humanizeSeconds(90000)).toBe("1 day, 1 hour")
        expect(humanizeSeconds(0)).toBe("0 seconds")
    })
})
