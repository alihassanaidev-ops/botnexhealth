import { describe, expect, it } from "vitest"

import {
    NODE_PMS,
    PALETTE_GROUPS,
    selectableTriggerTypes,
    TRIGGER_META,
    triggerAllowedForPms,
} from "@/lib/workflow/catalog"
import {
    NEXHEALTH_APPOINTMENT_CONTEXT_SAMPLE,
    sampleWorkflowContext,
} from "@/lib/workflow/context-fields"
import type { TriggerType } from "@/types/workflow"

const ALL_TRIGGERS = Object.keys(TRIGGER_META) as TriggerType[]

describe("PMS scoping of the workflow builder", () => {
    // The trigger-level PMS gate is gone. It existed to hide
    // `appointment_state_changed` from NexHealth tenants, because some of the
    // states it matched were GoTracker-only; per-PMS availability now lives on
    // individual event keys in the served event catalog, which is finer-grained.
    it("offers every trigger on every PMS, and while the PMS is still unknown", () => {
        for (const pms of ["gotracker", "nexhealth", null] as const) {
            const offered = selectableTriggerTypes("manual", pms)
            expect(offered).toEqual(ALL_TRIGGERS)
            for (const trigger of ALL_TRIGGERS) {
                expect(triggerAllowedForPms(trigger, pms)).toBe(true)
            }
        }
    })

    it("keeps an already-selected trigger visible so old workflows still render", () => {
        expect(selectableTriggerTypes("schedule", "nexhealth")).toContain("schedule")
    })

    // What survives the rearchitecture is the NODE-level gate: the GoTracker
    // appointment write-back binds a workflow to one practice-management system,
    // so it stays owned by GoTracker.
    it("keeps the GoTracker appointment node owned by GoTracker", () => {
        expect(NODE_PMS.update_gotracker_appointment).toEqual(["gotracker"])
        expect(NODE_PMS.update_appointment).toBeUndefined()
    })

    it("keeps the GoTracker-only node out of the palette entirely", () => {
        const palette = PALETTE_GROUPS.flatMap((group) => group.types)
        expect(palette).not.toContain("update_gotracker_appointment")
        expect(palette).toContain("update_appointment")
    })

    // Per-PMS field availability moved to the served event catalog, which keys
    // it on the individual field rather than on a hardcoded frontend list. The
    // equivalent assertions now live in the backend contract test, where they
    // can be checked against a real payload — see
    // `tests/unit/test_event_context_contract.py`.

    it("uses a NexHealth-shaped sample context for NexHealth", () => {
        const sample = sampleWorkflowContext("nexhealth")
        expect(sample).not.toHaveProperty("gotracker_payload")
        expect(sample.appointment_id).toBe(
            NEXHEALTH_APPOINTMENT_CONTEXT_SAMPLE.data.appointment_id,
        )
        expect(JSON.stringify(sample).toLowerCase()).not.toContain("gotracker")
    })
})
