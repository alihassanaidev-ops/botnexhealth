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
    it("accepts a Chair Flow state as the only appointment-state matcher", () => {
        const def = base()
        def.trigger = {
            type: "appointment_state_changed",
            status_ids: [],
            confirmed: null,
            preconfirmed: null,
            flow_states: ["Completed"],
            max_followup_delay_hours: 72,
            campaign_goal: "post_op_followup",
        }

        const issues = validateDefinition(def)

        expect(issues.some((issue) => issue.message.includes("at least one matcher"))).toBe(false)
    })

    it("rejects a post-op deadline outside the backend's 168-hour limit", () => {
        const def = base()
        def.trigger = {
            type: "appointment_state_changed",
            status_ids: [],
            confirmed: null,
            preconfirmed: null,
            flow_states: ["Completed"],
            max_followup_delay_hours: 169,
        }

        const issues = validateDefinition(def)

        expect(issues.some((issue) => issue.message.includes("0 to 168 hours"))).toBe(true)
    })

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

    it("validates drip batch size and interval", () => {
        const def: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "manual" },
            entry_node_id: "drip-1",
            nodes: [
                { type: "drip", id: "drip-1", batch_size: 0, interval_seconds: 0, next_node_id: "exit-1" },
                { type: "exit", id: "exit-1", outcome: "released" },
            ],
        }
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.node_id === "drip-1" && i.message.includes("batch size"))).toBe(true)
        expect(issues.some((i) => i.node_id === "drip-1" && i.message.includes("interval"))).toBe(true)
    })

    it("validates the response window for an SMS reply wait mode", () => {
        const def: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "manual" },
            entry_node_id: "wait-1",
            nodes: [
                {
                    type: "wait",
                    id: "wait-1",
                    wait_for: {
                        type: "sms_reply",
                        response_window_seconds: 30,
                        include_reply_key: true,
                        response_mappings: [],
                    },
                    next_node_id: "exit-1",
                },
                { type: "exit", id: "exit-1", outcome: "timed_out" },
            ],
        }

        const issues = validateDefinition(def)

        expect(issues.some((i) => i.node_id === "wait-1" && i.message.includes("response window"))).toBe(true)
    })

    it("flags out-of-range max_attempts", () => {
        const def = base()
        ;(def.nodes[0] as { max_attempts: number }).max_attempts = 9
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.message.includes("Max attempts"))).toBe(true)
    })

    it("flags a voice phone country override without a valid country", () => {
        const def: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "manual" },
            entry_node_id: "voice-1",
            nodes: [
                {
                    type: "send_voice",
                    id: "voice-1",
                    retell_agent_id: "",
                    voice_profile_id: "profile-1",
                    next_node_id: "exit-1",
                    phone_country_code_enabled: true,
                    phone_country_region: "",
                },
                { type: "exit", id: "exit-1", outcome: "done" },
            ],
        }
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.message.includes("Phone country override"))).toBe(true)
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

    it("warns when a merge field is unavailable for the trigger", () => {
        const def = base()
        def.trigger = { type: "manual" }
        ;(def.nodes[0] as { body_template: string }).body_template = "Hi {{appointment_date}}"
        const issues = validateDefinition(def)
        expect(
            issues.some((i) => i.severity === "warning" && i.message.includes("Unavailable merge field")),
        ).toBe(true)
    })

    it("warns when a merge field is unavailable for the message channel", () => {
        const def = base()
        ;(def.nodes[0] as { body_template: string }).body_template = "Hi {{location_address}}"
        const issues = validateDefinition(def)
        expect(
            issues.some((i) => i.severity === "warning" && i.message.includes("Unavailable merge field")),
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

    it("flags patient status triggers without statuses", () => {
        const def = base()
        def.trigger = { type: "patient_status_changed", statuses: [] }
        const issues = validateDefinition(def)
        expect(issues.some((i) => i.message.includes("Internal status trigger"))).toBe(true)
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
