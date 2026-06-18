import { describe, it, expect } from "vitest"
import { STATUS_COLORS, statusBadgeClasses, statusSwatchClass } from "@/lib/status-colors"

describe("status-colors", () => {
    it("maps a known palette key to its badge + swatch classes", () => {
        expect(statusBadgeClasses("emerald")).toContain("emerald")
        expect(statusSwatchClass("emerald")).toBe("bg-emerald-500")
    })

    it("falls back to the first palette entry for unknown/undefined keys", () => {
        const fallback = STATUS_COLORS[0]
        expect(statusBadgeClasses("not-a-color")).toBe(fallback.badge)
        expect(statusBadgeClasses(undefined)).toBe(fallback.badge)
        expect(statusSwatchClass(undefined)).toBe(fallback.swatch)
    })

    it("every palette key has full literal class strings (Tailwind-safe)", () => {
        for (const c of STATUS_COLORS) {
            expect(c.badge).toContain(`bg-${c.key}-500/15`)
            expect(c.swatch).toBe(`bg-${c.key}-500`)
        }
    })
})
