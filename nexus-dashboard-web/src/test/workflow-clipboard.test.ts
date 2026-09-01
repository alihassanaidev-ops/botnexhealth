import { describe, expect, it } from "vitest"
import {
    copyNodes,
    duplicateNodes,
    pasteNodes,
    searchNodes,
} from "@/lib/workflow/graph"
import type {
    ConditionNode,
    PatientRegistrationNode,
    WorkflowDefinition,
} from "@/types/workflow"

const DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "send-sms-1",
    nodes: [
        {
            type: "send_sms",
            id: "send-sms-1",
            body_template: "Hi {{patient_first_name}}, your implant consult is coming up.",
            next_node_id: "condition-1",
        },
        {
            type: "condition",
            id: "condition-1",
            rules: [{ field: "appointment_status", op: "eq", value: "booked" }],
            true_next_node_id: "exit-1",
            false_next_node_id: "exit-2",
        },
        { type: "exit", id: "exit-1", outcome: "confirmed" },
        { type: "exit", id: "exit-2", outcome: "no_answer" },
    ],
    layout: {
        "send-sms-1": { x: 0, y: 0 },
        "condition-1": { x: 0, y: 120 },
        "exit-1": { x: -100, y: 240 },
        "exit-2": { x: 100, y: 240 },
    },
}

describe("duplicate", () => {
    it("gives the copy fresh ids and leaves the original untouched", () => {
        const { def, newIds } = duplicateNodes(DEF, ["send-sms-1"])
        expect(newIds).toHaveLength(1)
        expect(newIds[0]).not.toBe("send-sms-1")
        expect(def.nodes).toHaveLength(5)
        expect(DEF.nodes).toHaveLength(4)
    })

    it("clears edges that would leave the copied set", () => {
        // A copy that silently rejoined the original graph is almost never what
        // "duplicate" means, and a dangling pointer is at least visible.
        const { def, newIds } = duplicateNodes(DEF, ["send-sms-1"])
        const copy = def.nodes.find((n) => n.id === newIds[0])
        expect(copy?.type === "send_sms" && copy.next_node_id).toBe("")
    })

    it("repoints edges that stay inside the copied set", () => {
        const { def, newIds } = duplicateNodes(DEF, ["condition-1", "exit-1", "exit-2"])
        const copy = def.nodes.find(
            (n) => n.id === newIds[0] && n.type === "condition",
        ) as ConditionNode
        expect(newIds).toContain(copy.true_next_node_id)
        expect(newIds).toContain(copy.false_next_node_id)
        expect(copy.true_next_node_id).not.toBe("exit-1")
    })

    it("offsets the copy so it does not sit on top of the original", () => {
        const { def, newIds } = duplicateNodes(DEF, ["send-sms-1"])
        const original = DEF.layout!["send-sms-1"]
        const copy = def.layout![newIds[0]]
        expect(copy.x).toBeGreaterThan(original.x)
        expect(copy.y).toBeGreaterThan(original.y)
    })

    it("is a no-op for an empty selection", () => {
        const { def, newIds } = duplicateNodes(DEF, [])
        expect(newIds).toEqual([])
        expect(def).toBe(DEF)
    })

    it("repoints a registration node's second exit, and nulls it when it leaves the set", () => {
        // `on_abandoned_node_id` is the one pointer that is not `next_node_id`,
        // so it is the one a blanket rewrite would miss. Optional pointers drop
        // to null rather than "", which is not a valid id.
        const withRegistration: WorkflowDefinition = {
            ...DEF,
            nodes: [
                ...DEF.nodes,
                {
                    type: "patient_registration",
                    id: "patient-registration-1",
                    next_node_id: "exit-1",
                    provider_id: "prov-1",
                    on_abandoned_node_id: "exit-2",
                },
            ],
        }

        const inSet = duplicateNodes(withRegistration, [
            "patient-registration-1",
            "exit-2",
        ])
        const kept = inSet.def.nodes.find(
            (n) => n.type === "patient_registration" && inSet.newIds.includes(n.id),
        ) as PatientRegistrationNode
        expect(inSet.newIds).toContain(kept.on_abandoned_node_id)
        expect(kept.on_abandoned_node_id).not.toBe("exit-2")
        // `next_node_id` pointed outside the set, so it is cleared.
        expect(kept.next_node_id).toBe("")

        const outOfSet = duplicateNodes(withRegistration, ["patient-registration-1"])
        const dropped = outOfSet.def.nodes.find(
            (n) => n.type === "patient_registration" && outOfSet.newIds.includes(n.id),
        ) as PatientRegistrationNode
        expect(dropped.on_abandoned_node_id).toBeNull()
    })
})

describe("copy and paste", () => {
    it("round-trips a selection into new nodes", () => {
        const clipboard = copyNodes(DEF, ["exit-1", "exit-2"])
        expect(clipboard?.nodes).toHaveLength(2)

        const { def, newIds } = pasteNodes(DEF, clipboard!)
        expect(def.nodes).toHaveLength(6)
        expect(newIds).toHaveLength(2)
        expect(newIds).not.toContain("exit-1")
    })

    it("can paste the same clipboard twice without an id collision", () => {
        const clipboard = copyNodes(DEF, ["exit-1"])
        const first = pasteNodes(DEF, clipboard!)
        const second = pasteNodes(first.def, clipboard!)
        expect(second.newIds[0]).not.toBe(first.newIds[0])
        expect(new Set(second.def.nodes.map((n) => n.id)).size).toBe(second.def.nodes.length)
    })

    it("returns null when nothing is selected", () => {
        expect(copyNodes(DEF, [])).toBeNull()
    })
})

describe("search", () => {
    it("finds a node by its message content", () => {
        expect(searchNodes(DEF, "implant").map((n) => n.id)).toEqual(["send-sms-1"])
    })

    it("finds a node by id and by type", () => {
        expect(searchNodes(DEF, "condition-1").map((n) => n.id)).toEqual(["condition-1"])
        expect(searchNodes(DEF, "exit").map((n) => n.id)).toEqual(["exit-1", "exit-2"])
    })

    it("finds an exit by its outcome", () => {
        expect(searchNodes(DEF, "no_answer").map((n) => n.id)).toEqual(["exit-2"])
    })

    it("is case-insensitive and ignores an empty query", () => {
        expect(searchNodes(DEF, "IMPLANT")).toHaveLength(1)
        expect(searchNodes(DEF, "   ")).toEqual([])
    })
})
