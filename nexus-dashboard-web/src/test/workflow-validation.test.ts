import { describe, it, expect } from "vitest"
import {
    isPublishable,
    unreachableNodes,
    validateDefinition,
} from "@/lib/workflow/validation"
import type { WorkflowDefinition } from "@/types/workflow"

function base(): WorkflowDefinition {
    return {
        schema_version: "1.0",
        trigger: { type: "appointment_offset", offset_hours: -24 },
        entry_node_id: "sms-1",
        nodes: [
            {
                type: "send_sms",
                id: "sms-1",
                body_template: "Hi {{patient_first_name}}, reply STOP to opt out.",
                next_node_id: "exit-1",
                max_attempts: 1,
            },
            { type: "exit", id: "exit-1", outcome: "sent" },
        ],
    }
}

describe("workflow validation", () => {
    it("a well-formed workflow has no errors", () => {
        const issues = validateDefinition(base())
        expect(issues.filter((i) => i.severity === "error")).toHaveLength(0)
        expect(isPublishable(issues)).toBe(true)
    })

    it("requires at least one exit", () => {
        const def = base()
        def.nodes = [def.nodes[0]] // drop the exit
        def.nodes[0] = { ...def.nodes[0], next_node_id: "sms-1" } as typeof def.nodes[0]
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.message.includes("Exit"))).toBe(true)
        expect(isPublishable(issues)).toBe(false)
    })

    it("flags a dangling next pointer", () => {
        const def = base()
        ;(def.nodes[0] as { next_node_id: string }).next_node_id = "nope"
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.node_id === "sms-1" && i.message.includes("missing"))).toBe(true)
    })

    it("flags an empty SMS body", () => {
        const def = base()
        ;(def.nodes[0] as { body_template: string }).body_template = "   "
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.node_id === "sms-1" && i.message.includes("body is empty"))).toBe(true)
    })

    it("flags out-of-range max_attempts", () => {
        const def = base()
        ;(def.nodes[0] as { max_attempts: number }).max_attempts = 9
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.message.includes("Max attempts"))).toBe(true)
    })

    it("flags duplicate ids", () => {
        const def = base()
        def.nodes.push({ type: "exit", id: "sms-1", outcome: null })
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.message.includes("Duplicate"))).toBe(true)
    })

    it("warns on unknown merge fields", () => {
        const def = base()
        ;(def.nodes[0] as { body_template: string }).body_template = "Hi {{unknown_field}}"
        const issues = validateDefinition(def)
        expect(
            issues.some((i) => i.severity === "warning" && i.message.includes("Unknown merge field")),
        ).toBe(true)
    })

    it("warns on unreachable nodes", () => {
        const def = base()
        def.nodes.push({ type: "exit", id: "orphan", outcome: null })
        expect(unreachableNodes(def)).toContain("orphan")
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.node_id === "orphan" && i.severity === "warning")).toBe(true)
    })

    it("flags a recall interval below 1", () => {
        const def = base()
        def.trigger = { type: "recall_scan", recall_interval_months: 0 }
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.message.includes("Recall interval"))).toBe(true)
    })

    it("flags condition branches that are not connected", () => {
        const def: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "manual" },
            entry_node_id: "cond-1",
            nodes: [
                {
                    type: "condition",
                    id: "cond-1",
                    logic: "AND",
                    rules: [{ field: "confirmed", op: "eq", value: true }],
                    true_next_node_id: "exit-1",
                    false_next_node_id: "",
                },
                { type: "exit", id: "exit-1", outcome: "ok" },
            ],
        }
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.node_id === "cond-1" && i.message.includes("No branch"))).toBe(true)
    })

    it("sorts errors before warnings", () => {
        const def = base()
        ;(def.nodes[0] as { body_template: string }).body_template = "Hi {{unknown_field}}"
        ;(def.nodes[0] as { next_node_id: string }).next_node_id = "nope"
        const issues = validateDefinition(def)
        const firstWarning = issues.findIndex((i) => i.severity === "warning")
        const lastError = issues.map((i) => i.severity).lastIndexOf("error")
        expect(lastError).toBeLessThan(firstWarning)
    })
})
