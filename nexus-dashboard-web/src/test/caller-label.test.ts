/**
 * An unnamed caller must be labelled by their number, not by a placeholder.
 *
 * The call list already receives the number — in full for no-PMS location
 * admins, masked to the last four digits for everyone else — so rendering
 * "Unknown caller" threw away identifying information the row already had, and
 * made every anonymous call look like the same one.
 */

import { describe, expect, it } from "vitest"

import { callerLabel } from "@/components/calls/format"

describe("callerLabel", () => {
    it("prefers the contact name when there is one", () => {
        expect(callerLabel("Dania Elkassem", "+15198590292")).toEqual({
            text: "Dania Elkassem",
            kind: "name",
        })
    })

    it("falls back to a full number for no-PMS location admins", () => {
        expect(callerLabel(null, "+15198590292")).toEqual({
            text: "+15198590292",
            kind: "phone",
        })
    })

    it("falls back to the masked number for every other role", () => {
        // mask_phone() keeps the last four digits; still enough to tell two
        // anonymous callers apart in a list.
        expect(callerLabel(null, "+*******0292")).toEqual({
            text: "+*******0292",
            kind: "phone",
        })
    })

    it("treats a blank or whitespace-only name as no name", () => {
        expect(callerLabel("   ", "+15198590292").kind).toBe("phone")
        expect(callerLabel(undefined, "+15198590292").kind).toBe("phone")
    })

    it('rejects the backend\'s "Unknown" phone sentinel', () => {
        // mask_phone() returns the literal "Unknown" when it had no digits to
        // mask — showing that in place of a name would be worse, not better.
        expect(callerLabel(null, "Unknown")).toEqual({
            text: "Unknown caller",
            kind: "unknown",
        })
    })

    it("only says Unknown caller when there is genuinely nothing", () => {
        expect(callerLabel(null, null)).toEqual({
            text: "Unknown caller",
            kind: "unknown",
        })
        expect(callerLabel(null, "  ").kind).toBe("unknown")
    })
})
