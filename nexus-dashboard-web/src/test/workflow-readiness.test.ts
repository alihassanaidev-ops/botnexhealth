import { describe, it, expect } from "vitest"
import {
    hasUnreadyChannel,
    readinessIssues,
    usedChannelStatuses,
} from "@/lib/workflow/readiness"
import { channelsUsed } from "@/lib/workflow/graph"
import type { ChannelReadiness, WorkflowDefinition } from "@/types/workflow"

function def(nodes: WorkflowDefinition["nodes"]): WorkflowDefinition {
    return {
        schema_version: "1.0",
        trigger: { type: "manual" },
        entry_node_id: nodes[0]?.id ?? "exit-1",
        nodes,
    }
}

const READINESS: ChannelReadiness = {
    sms: true,
    email: false,
    voice_configurable: false,
    details: [
        { channel: "sms", ready: true, reason: null },
        { channel: "email", ready: false, reason: "No email from-address configured." },
        { channel: "voice", ready: false, reason: "No outbound voice agent." },
    ],
}

describe("channelsUsed", () => {
    it("collects only the channels the definition's send nodes target", () => {
        const d = def([
            { type: "send_sms", id: "sms-1", body_template: "hi", next_node_id: "email-1" },
            {
                type: "send_email",
                id: "email-1",
                subject_template: "s",
                body_template: "b",
                next_node_id: "exit-1",
            },
            { type: "exit", id: "exit-1", outcome: "done" },
        ])
        expect([...channelsUsed(d)].sort()).toEqual(["email", "sms"])
    })

    it("is empty when no send nodes are present", () => {
        const d = def([{ type: "exit", id: "exit-1", outcome: "done" }])
        expect(channelsUsed(d).size).toBe(0)
    })
})

describe("usedChannelStatuses", () => {
    it("resolves ready/reason per used channel and omits unused channels", () => {
        const d = def([
            { type: "send_sms", id: "sms-1", body_template: "hi", next_node_id: "voice-1" },
            { type: "send_voice", id: "voice-1", retell_agent_id: "", next_node_id: "exit-1" },
            { type: "exit", id: "exit-1", outcome: "done" },
        ])
        const statuses = usedChannelStatuses(d, READINESS)
        expect(statuses.map((s) => s.channel)).toEqual(["sms", "voice"])
        expect(statuses.find((s) => s.channel === "sms")).toMatchObject({ ready: true })
        expect(statuses.find((s) => s.channel === "voice")).toMatchObject({
            ready: false,
            reason: "No outbound voice agent.",
        })
        // Email is not used by this definition, so it is not reported.
        expect(statuses.some((s) => s.channel === "email")).toBe(false)
    })
})

describe("readinessIssues / hasUnreadyChannel", () => {
    it("emits a warning only for unready channels, carrying the reason as the fix", () => {
        const d = def([
            {
                type: "send_email",
                id: "email-1",
                subject_template: "s",
                body_template: "b",
                next_node_id: "exit-1",
            },
            { type: "exit", id: "exit-1", outcome: "done" },
        ])
        const statuses = usedChannelStatuses(d, READINESS)
        const issues = readinessIssues(statuses)
        expect(issues).toHaveLength(1)
        expect(issues[0]).toMatchObject({
            node_id: null,
            severity: "warning",
            message: "Email is not set up for this location.",
            fix: "No email from-address configured.",
        })
        expect(hasUnreadyChannel(statuses)).toBe(true)
    })

    it("emits nothing when every used channel is ready", () => {
        const d = def([
            { type: "send_sms", id: "sms-1", body_template: "hi", next_node_id: "exit-1" },
            { type: "exit", id: "exit-1", outcome: "done" },
        ])
        const statuses = usedChannelStatuses(d, READINESS)
        expect(readinessIssues(statuses)).toEqual([])
        expect(hasUnreadyChannel(statuses)).toBe(false)
    })
})
