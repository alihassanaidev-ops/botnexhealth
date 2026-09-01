import { describe, it, expect } from "vitest"
import { humanizeSeconds, simulateRun } from "@/lib/workflow/test-run"
import type { WorkflowDefinition } from "@/types/workflow"

const LINEAR: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "appointment_offset", offset_hours: -24 },
    entry_node_id: "sms-1",
    nodes: [
        {
            type: "send_sms",
            id: "sms-1",
            body_template: "Hi {{patient_first_name}}",
            next_node_id: "exit-1",
        },
        { type: "exit", id: "exit-1", outcome: "sent" },
    ],
}

const BRANCHED: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "cond-1",
    nodes: [
        {
            type: "condition",
            id: "cond-1",
            logic: "AND",
            rules: [{ field: "confirmed", op: "eq", value: true }],
            true_next_node_id: "exit-yes",
            false_next_node_id: "sms-1",
        },
        { type: "send_sms", id: "sms-1", body_template: "Reminder", next_node_id: "exit-no" },
        { type: "exit", id: "exit-yes", outcome: "confirmed" },
        { type: "exit", id: "exit-no", outcome: "reminded" },
    ],
}

describe("dry-run simulation", () => {
    it("walks a linear workflow to its exit and renders messages", () => {
        const result = simulateRun(LINEAR)
        expect(result.steps.map((s) => s.node_type)).toEqual(["send_sms", "exit"])
        expect(result.steps[0].detail).toBe("Hi Jordan")
        expect(result.outcome).toBe("sent")
        expect(result.truncated).toBe(false)
    })

    it("takes the true branch of a condition by default", () => {
        const result = simulateRun(BRANCHED)
        expect(result.outcome).toBe("confirmed")
    })

    it("describes drip actions in the simulated path", () => {
        const drip: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "manual" },
            entry_node_id: "drip-1",
            nodes: [
                { type: "drip", id: "drip-1", batch_size: 25, interval_seconds: 3600, next_node_id: "exit-1" },
                { type: "exit", id: "exit-1", outcome: "released" },
            ],
        }
        const result = simulateRun(drip)
        expect(result.steps.map((s) => s.node_type)).toEqual(["drip", "exit"])
        expect(result.steps[0].detail).toContain("25 contact(s)")
    })

    it("describes campaign booking and follows the booked preview branch", () => {
        const booking: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "manual" },
            entry_node_id: "book-1",
            nodes: [
                {
                    type: "book_appointment",
                    id: "book-1",
                    appointment_type_id: "type-1",
                    provider_id: "provider-1",
                    start_time: "{{booking_start_time}}",
                    booked_next_node_id: "booked",
                    could_not_book_next_node_id: "no-slot",
                    pending_next_node_id: "pending",
                },
                { type: "exit", id: "booked", outcome: "booked" },
                { type: "exit", id: "no-slot", outcome: "could_not_book" },
                { type: "exit", id: "pending", outcome: "pending" },
            ],
        }

        const result = simulateRun(booking, {
            data: { booking_start_time: "2026-09-02T14:30:00-04:00" },
        })

        expect(result.steps.map((s) => s.node_type)).toEqual(["book_appointment", "exit"])
        expect(result.steps[0].summary).toBe("Book appointment in PMS")
        expect(result.outcome).toBe("booked")
    })

    it("honors an explicit false branch choice", () => {
        const result = simulateRun(BRANCHED, { conditionChoices: { "cond-1": false } })
        expect(result.steps.map((s) => s.node_id)).toEqual(["cond-1", "sms-1", "exit-no"])
        expect(result.outcome).toBe("reminded")
    })

    it("truncates on a cycle instead of looping forever", () => {
        const cyclic: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "manual" },
            entry_node_id: "wait-1",
            nodes: [
                {
                    type: "wait",
                    id: "wait-1",
                    wait_for: {
                        type: "time",
                        delay: { delay_type: "duration", duration_seconds: 60 },
                    },
                    next_node_id: "wait-1",
                },
                { type: "exit", id: "exit-1", outcome: "never" },
            ],
        }
        const result = simulateRun(cyclic)
        expect(result.truncated).toBe(true)
        expect(result.outcome).toBeNull()
    })

    it("reports a dead end when a pointer references a missing node", () => {
        const broken: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "manual" },
            entry_node_id: "sms-1",
            nodes: [
                { type: "send_sms", id: "sms-1", body_template: "x", next_node_id: "ghost" },
                { type: "exit", id: "exit-1", outcome: "sent" },
            ],
        }
        const result = simulateRun(broken)
        expect(result.steps[result.steps.length - 1]?.summary).toBe("Dead end")
        expect(result.outcome).toBeNull()
    })
})

describe("humanizeSeconds", () => {
    it("formats durations", () => {
        expect(humanizeSeconds(3600)).toBe("1 hour")
        expect(humanizeSeconds(90000)).toBe("1 day, 1 hour")
        expect(humanizeSeconds(0)).toBe("0 seconds")
    })
})
