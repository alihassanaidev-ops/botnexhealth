import { describe, expect, it } from "vitest"

import { selectableTriggerTypes, triggerAllowedForPms } from "@/lib/workflow/catalog"
import {
    contextFieldsForTrigger,
    NEXHEALTH_APPOINTMENT_CONTEXT_SAMPLE,
    sampleWorkflowContext,
} from "@/lib/workflow/context-fields"

describe("PMS scoping of the workflow builder", () => {
    it("hides the GoTracker-only trigger from NexHealth institutions", () => {
        const offered = selectableTriggerTypes("manual", "nexhealth")
        expect(offered).not.toContain("appointment_state_changed")
        expect(offered).toContain("appointment_offset")
    })

    it("offers the appointment-state trigger to GoTracker institutions", () => {
        expect(selectableTriggerTypes("manual", "gotracker")).toContain(
            "appointment_state_changed",
        )
    })

    it("fails closed while the PMS type is unknown", () => {
        expect(triggerAllowedForPms("appointment_state_changed", null)).toBe(false)
        expect(selectableTriggerTypes("manual", null)).not.toContain(
            "appointment_state_changed",
        )
    })

    it("keeps an already-selected trigger visible so old workflows still render", () => {
        expect(
            selectableTriggerTypes("appointment_state_changed", "nexhealth"),
        ).toContain("appointment_state_changed")
    })

    it("serves no GoTracker context fields to a NexHealth institution", () => {
        const fields = contextFieldsForTrigger("appointment_offset", "nexhealth")
        expect(fields.length).toBeGreaterThan(0)
        for (const field of fields) {
            expect(field.name).not.toMatch(/gotracker/)
            expect(field.label).not.toMatch(/GoTracker/)
        }
        expect(fields.map((f) => f.name)).toContain("appointment_id")
    })

    it("still serves the GoTracker payload fields to GoTracker institutions", () => {
        const names = contextFieldsForTrigger("appointment_offset", "gotracker").map(
            (f) => f.name,
        )
        expect(names).toContain("gotracker_appointment_id")
        expect(names).not.toContain("appointment_id")
    })

    it("uses a NexHealth-shaped sample context for NexHealth", () => {
        const sample = sampleWorkflowContext("nexhealth")
        expect(sample).not.toHaveProperty("gotracker_payload")
        expect(sample.appointment_id).toBe(
            NEXHEALTH_APPOINTMENT_CONTEXT_SAMPLE.data.appointment_id,
        )
        expect(JSON.stringify(sample).toLowerCase()).not.toContain("gotracker")
    })
})
