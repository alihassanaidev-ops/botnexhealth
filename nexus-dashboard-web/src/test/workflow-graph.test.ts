import { describe, it, expect } from "vitest"
import {
    addNode,
    blankDefinition,
    computeDepths,
    createNode,
    createTrigger,
    definitionToFlow,
    genId,
    outgoing,
    referencedIds,
    removeNode,
    serializeDefinition,
    setEntry,
    TRIGGER_NODE_ID,
    updateNode,
} from "@/lib/workflow/graph"
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
            next_node_id: "wait-1",
        },
        {
            type: "wait",
            id: "wait-1",
            delay: { delay_type: "duration", duration_seconds: 3600 },
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
            false_next_node_id: "exit-no",
        },
        { type: "exit", id: "exit-yes", outcome: "confirmed" },
        { type: "exit", id: "exit-no", outcome: "reminded" },
    ],
}

describe("workflow graph — derivation", () => {
    it("derives a trigger node + one node per definition node", () => {
        const { nodes } = definitionToFlow(LINEAR)
        expect(nodes).toHaveLength(LINEAR.nodes.length + 1)
        expect(nodes.find((n) => n.id === TRIGGER_NODE_ID)).toBeTruthy()
    })

    it("creates a trigger->entry edge and linear next edges", () => {
        const { edges } = definitionToFlow(LINEAR)
        expect(edges.some((e) => e.source === TRIGGER_NODE_ID && e.target === "sms-1")).toBe(true)
        expect(edges.some((e) => e.source === "sms-1" && e.target === "wait-1")).toBe(true)
        expect(edges.some((e) => e.source === "wait-1" && e.target === "exit-1")).toBe(true)
    })

    it("condition nodes emit two handled edges (true/false)", () => {
        const { edges } = definitionToFlow(BRANCHED)
        const out = edges.filter((e) => e.source === "cond-1")
        expect(out).toHaveLength(2)
        expect(out.some((e) => e.sourceHandle === "true" && e.target === "exit-yes")).toBe(true)
        expect(out.some((e) => e.sourceHandle === "false" && e.target === "exit-no")).toBe(true)
    })

    it("lays out nodes in columns by depth (trigger=0, entry=1)", () => {
        const depths = computeDepths(LINEAR)
        expect(depths.get(TRIGGER_NODE_ID)).toBe(0)
        expect(depths.get("sms-1")).toBe(1)
        expect(depths.get("wait-1")).toBe(2)
        expect(depths.get("exit-1")).toBe(3)
        const { nodes } = definitionToFlow(LINEAR)
        const sms = nodes.find((n) => n.id === "sms-1")!
        const wait = nodes.find((n) => n.id === "wait-1")!
        expect(wait.position.x).toBeGreaterThan(sms.position.x)
    })

    it("flags the entry node in flow data", () => {
        const { nodes } = definitionToFlow(LINEAR)
        const sms = nodes.find((n) => n.id === "sms-1")!
        expect(sms.data.kind === "step" && sms.data.isEntry).toBe(true)
    })

    it("places unreachable nodes in a trailing column instead of dropping them", () => {
        const withOrphan: WorkflowDefinition = {
            ...LINEAR,
            nodes: [...LINEAR.nodes, { type: "exit", id: "orphan", outcome: null }],
        }
        const depths = computeDepths(withOrphan)
        expect(depths.get("orphan")).toBeGreaterThan(depths.get("exit-1")!)
        const { nodes } = definitionToFlow(withOrphan)
        expect(nodes.find((n) => n.id === "orphan")).toBeTruthy()
    })
})

describe("workflow graph — pointer helpers", () => {
    it("outgoing returns one target for linear, two for condition, none for exit", () => {
        expect(outgoing(LINEAR.nodes[0])).toHaveLength(1)
        expect(outgoing(BRANCHED.nodes[0])).toHaveLength(2)
        expect(outgoing(LINEAR.nodes[2])).toHaveLength(0)
    })
    it("referencedIds omits empty pointers", () => {
        const node = createNode("send_sms", "x")
        expect(referencedIds(node)).toEqual([])
    })
})

describe("workflow graph — factories", () => {
    it("genId produces unique kebab ids", () => {
        expect(genId("send_sms", ["send-sms-1"])).toBe("send-sms-2")
        expect(genId("condition", [])).toBe("condition-1")
    })
    it("createNode yields schema-shaped defaults", () => {
        expect(createNode("wait", "w").type).toBe("wait")
        expect(createNode("send_sms", "s")).toMatchObject({ max_attempts: 1, body_template: "" })
        expect(createNode("condition", "c")).toMatchObject({ logic: "AND" })
    })
    it("createTrigger yields sensible defaults", () => {
        expect(createTrigger("appointment_offset")).toMatchObject({ offset_hours: -24 })
        expect(createTrigger("recall_scan")).toMatchObject({ recall_interval_months: 6 })
        expect(createTrigger("manual").type).toBe("manual")
    })
    it("blankDefinition is a valid minimal graph", () => {
        const def = blankDefinition()
        expect(def.nodes).toHaveLength(1)
        expect(def.entry_node_id).toBe(def.nodes[0].id)
    })
})

describe("workflow graph — mutations", () => {
    it("addNode appends immutably", () => {
        const next = addNode(LINEAR, createNode("exit", "exit-2"))
        expect(next.nodes).toHaveLength(4)
        expect(LINEAR.nodes).toHaveLength(3)
    })
    it("updateNode replaces by id", () => {
        const replacement = { ...LINEAR.nodes[2], outcome: "changed" as string | null }
        const next = updateNode(LINEAR, "exit-1", replacement)
        expect(next.nodes.find((n) => n.id === "exit-1")).toMatchObject({ outcome: "changed" })
    })
    it("removeNode bypasses linear predecessors to the removed node's next", () => {
        const next = removeNode(LINEAR, "wait-1")
        expect(next.nodes.find((n) => n.id === "wait-1")).toBeUndefined()
        const sms = next.nodes.find((n) => n.id === "sms-1")!
        expect(sms.type === "send_sms" && sms.next_node_id).toBe("exit-1")
    })
    it("removeNode on the entry node repoints entry to the bypass target", () => {
        const next = removeNode(LINEAR, "sms-1")
        expect(next.entry_node_id).toBe("wait-1")
    })
    it("setEntry updates the entry pointer", () => {
        expect(setEntry(LINEAR, "wait-1").entry_node_id).toBe("wait-1")
    })
    it("serializeDefinition always stamps schema_version", () => {
        expect(serializeDefinition(LINEAR).schema_version).toBe("1.0")
    })
})
