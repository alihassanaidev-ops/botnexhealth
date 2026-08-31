/**
 * The Tags filter must offer only the vocabulary the tenant's agent can emit.
 *
 * Both vocabularies live in one registry because rendering needs all of them —
 * a call keeps its label and colour even if the clinic later switches modes.
 * The filter menu is the one place that must narrow, and these tests pin that
 * split so the two concerns can't collapse back into each other.
 */

import { describe, expect, it } from "vitest"

import {
    STATUS_OPTIONS,
    callStatusFilterOptions,
    type CallStatusOption,
} from "@/lib/constants"

const values = (options: CallStatusOption[]) => options.map((o) => o.value)

describe("callStatusFilterOptions", () => {
    it("hides no-PMS request statuses from a PMS tenant", () => {
        const pms = values(callStatusFilterOptions(false))

        expect(pms).toContain("appointment_booked")
        expect(pms).toContain("insurance_verified")
        // A PMS agent books directly; it never files a request for staff.
        expect(pms).not.toContain("needs_booking")
        expect(pms).not.toContain("needs_reschedule")
        expect(pms).not.toContain("needs_cancellation")
        expect(pms).not.toContain("insurance_and_billing")
    })

    it("hides PMS transaction statuses from a no-PMS tenant", () => {
        const noPms = values(callStatusFilterOptions(true))

        expect(noPms).toContain("needs_booking")
        expect(noPms).toContain("insurance_and_billing")
        // Nothing was transacted in a PMS, because there is no PMS.
        expect(noPms).not.toContain("appointment_booked")
        expect(noPms).not.toContain("appointment_rescheduled")
        expect(noPms).not.toContain("appointment_cancelled")
        expect(noPms).not.toContain("insurance_verified")
        expect(noPms).not.toContain("insurance_unverified")
    })

    it("keeps the outcomes both agents emit in either mode", () => {
        const shared = [
            "emergency",
            "complaint",
            "needs_callback",
            "faq_handled",
            "financial_inquiry",
            "transferred",
            "no_action_needed",
        ]
        const pms = values(callStatusFilterOptions(false))
        const noPms = values(callStatusFilterOptions(true))

        for (const value of shared) {
            expect(pms).toContain(value)
            expect(noPms).toContain(value)
        }
    })

    it("covers the whole registry across the two modes", () => {
        // No status may be unreachable from every filter menu — that would be a
        // value a call can carry but nobody can ever filter for.
        const reachable = new Set([
            ...values(callStatusFilterOptions(false)),
            ...values(callStatusFilterOptions(true)),
        ])

        expect(reachable).toEqual(new Set(values(STATUS_OPTIONS)))
    })

    it("leaves the full registry intact for label and colour lookups", () => {
        // Rendering must still resolve a status from the *other* vocabulary —
        // e.g. historical calls from before a clinic switched modes.
        const byValue = new Map(STATUS_OPTIONS.map((o) => [o.value, o]))

        expect(byValue.get("appointment_booked")?.label).toBe("Appointment Booked")
        expect(byValue.get("needs_booking")?.label).toBe("Needs Booking")
        expect(byValue.get("insurance_and_billing")?.color).toBeTruthy()
    })
})
