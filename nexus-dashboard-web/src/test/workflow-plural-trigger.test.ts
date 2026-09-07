/**
 * The builder must read a definition in the shape the database now holds.
 *
 * The trigger migration rewrote every stored definition from `trigger` to
 * `triggers` and dropped the singular key. This model stayed singular, because
 * around a hundred call sites read `def.trigger` — so the first of those was
 * `undefined.type` and the whole builder page died with
 * "can't access property 'type', t.trigger is undefined".
 */
import { describe, it, expect } from "vitest"
import { normalizeDefinition, serializeDefinition } from "@/lib/workflow/graph"
import { validateDefinition } from "@/lib/workflow/validation"
import type { WorkflowDefinition } from "@/types/workflow"

// Exactly what the migration left for "Surgery Pre-Appointment Switch".
const AS_STORED = {
    schema_version: "1.0",
    triggers: [
        {
            type: "event",
            event_keys: ["appointment.reminder_due"],
            reminder_offset_hours: -24,
        },
    ],
    entry_node_id: "s1",
    nodes: [
        { id: "s1", type: "send_sms", body_template: "Hi", next_node_id: "x1" },
        { id: "x1", type: "exit", outcome: "done" },
    ],
} as unknown as WorkflowDefinition

describe("a definition stored with plural triggers", () => {
    it("exposes a singular trigger the builder can read", () => {
        const def = normalizeDefinition(AS_STORED)
        expect(def.trigger).toBeDefined()
        expect(def.trigger.type).toBe("event")
    })

    it("keeps the trigger's own configuration", () => {
        const trigger = normalizeDefinition(AS_STORED).trigger
        expect(trigger.type === "event" && trigger.reminder_offset_hours).toBe(-24)
        expect(trigger.type === "event" && trigger.event_keys).toEqual([
            "appointment.reminder_due",
        ])
    })

    it("validates without throwing, as the builder does on load", () => {
        expect(() => validateDefinition(normalizeDefinition(AS_STORED))).not.toThrow()
    })

    it("saves back as plural, so an edit does not undo the migration", () => {
        const saved = serializeDefinition(normalizeDefinition(AS_STORED)) as unknown as
            Record<string, unknown>
        expect(saved.triggers).toHaveLength(1)
        expect(saved.trigger).toBeUndefined()
    })

    it("still accepts a definition that has only the singular key", () => {
        // Anything saved by an older build, and every unit fixture.
        const legacy = {
            ...AS_STORED,
            trigger: { type: "manual" },
            triggers: undefined,
        } as unknown as WorkflowDefinition
        expect(normalizeDefinition(legacy).trigger.type).toBe("manual")
    })
})
