import { describe, it, expect } from "vitest"
import {
    addNode,
    addVoiceOutcomeBranch,
    autoLayoutDefinition,
    blankDefinition,
    clearLayout,
    computeDepths,
    connectNodes,
    createNode,
    createTrigger,
    definitionToFlow,
    genId,
    outgoing,
    referencedIds,
    removeNode,
    serializeDefinition,
    setEntry,
    setNodePosition,
    TRIGGER_NODE_ID,
    updateNode,
    VOICE_OUTCOME_BRANCH_VALUES,
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

    it("uses compact smoothstep routing for linear and branch edges", () => {
        const { edges } = definitionToFlow(LINEAR)
        expect(edges.every((e) => e.type === "smoothstep")).toBe(true)
        expect(edges.every((e) => e.pathOptions && typeof e.pathOptions === "object")).toBe(true)
        expect(edges.every((e) => e.pathOptions?.borderRadius === 8)).toBe(true)
        expect(edges.every((e) => e.pathOptions?.offset === 14)).toBe(true)
        expect(edges.every((e) => e.interactionWidth === 18)).toBe(true)

        const branched = definitionToFlow(BRANCHED).edges.filter((e) => e.source === "cond-1")
        expect(branched.every((e) => e.type === "smoothstep")).toBe(true)
        expect(branched.every((e) => e.pathOptions && typeof e.pathOptions === "object")).toBe(true)
        expect(branched.every((e) => e.pathOptions?.borderRadius === 8)).toBe(true)
        expect(branched.some((e) => e.sourceHandle === "true" && e.pathOptions?.offset === 22)).toBe(true)
        expect(branched.some((e) => e.sourceHandle === "false" && e.pathOptions?.offset === 30)).toBe(true)
    })

    it("condition nodes emit two handled edges (true/false)", () => {
        const { edges } = definitionToFlow(BRANCHED)
        const out = edges.filter((e) => e.source === "cond-1")
        expect(out).toHaveLength(2)
        expect(out.some((e) => e.sourceHandle === "true" && e.target === "exit-yes")).toBe(true)
        expect(out.some((e) => e.sourceHandle === "false" && e.target === "exit-no")).toBe(true)
        expect(out.every((e) => e.labelShowBg)).toBe(true)
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
        expect(createNode("send_voice", "v")).toMatchObject({ wait_for_outcome: false, max_attempts: 1 })
        expect(createNode("condition", "c")).toMatchObject({ logic: "AND" })
    })
    it("createTrigger yields sensible defaults", () => {
        expect(createTrigger("appointment_offset")).toMatchObject({ offset_hours: -24 })
        expect(createTrigger("recall_scan")).toMatchObject({ recall_interval_months: 6 })
        expect(createTrigger("manual").type).toBe("manual")
        expect(createTrigger("bulk_import").type).toBe("bulk_import")
        expect(createTrigger("callback_requested").type).toBe("callback_requested")
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

describe("workflow graph — drag-to-connect (Phase 4)", () => {
    it("connecting from a linear/send node sets its next_node_id", () => {
        const next = connectNodes(LINEAR, "sms-1", "exit-1")
        const sms = next.nodes.find((n) => n.id === "sms-1")!
        expect(sms.type === "send_sms" && sms.next_node_id).toBe("exit-1")
    })

    it("connecting from a condition node's true/false handle sets the matching branch", () => {
        const t = connectNodes(BRANCHED, "cond-1", "exit-no", "true")
        const f = connectNodes(BRANCHED, "cond-1", "exit-yes", "false")
        const tCond = t.nodes.find((n) => n.id === "cond-1")!
        const fCond = f.nodes.find((n) => n.id === "cond-1")!
        expect(tCond.type === "condition" && tCond.true_next_node_id).toBe("exit-no")
        // Untouched branch preserved.
        expect(tCond.type === "condition" && tCond.false_next_node_id).toBe("exit-no")
        expect(fCond.type === "condition" && fCond.false_next_node_id).toBe("exit-yes")
    })

    it("connecting from the synthetic trigger repoints entry_node_id", () => {
        const next = connectNodes(LINEAR, TRIGGER_NODE_ID, "wait-1")
        expect(next.entry_node_id).toBe("wait-1")
    })

    it("connecting from an exit node is a no-op", () => {
        expect(connectNodes(LINEAR, "exit-1", "sms-1")).toBe(LINEAR)
    })

    it("is immutable — the original definition is untouched", () => {
        connectNodes(LINEAR, "sms-1", "exit-1")
        expect((LINEAR.nodes[0] as { next_node_id: string }).next_node_id).toBe("wait-1")
    })
})

describe("workflow graph — voice outcome branch helper", () => {
    it("adds a call_outcome condition and staff handoff fallback after a voice node", () => {
        const def: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "callback_requested" },
            entry_node_id: "voice-1",
            nodes: [
                {
                    type: "send_voice",
                    id: "voice-1",
                    retell_agent_id: "agent-1",
                    next_node_id: "exit-1",
                    wait_for_outcome: false,
                },
                { type: "exit", id: "exit-1", outcome: "done" },
            ],
        }

        const next = addVoiceOutcomeBranch(def, "voice-1")
        const voice = next.nodes.find((n) => n.id === "voice-1")
        const condition = next.nodes.find((n) => n.type === "condition")
        const handoff = next.nodes.find((n) => n.type === "exit" && n.outcome === "staff_handoff")

        expect(VOICE_OUTCOME_BRANCH_VALUES).toContain("booked")
        expect(voice?.type === "send_voice" && voice.wait_for_outcome).toBe(true)
        expect(voice?.type === "send_voice" && voice.next_node_id).toBe(condition?.id)
        expect(condition?.type === "condition" && condition.rules[0]).toMatchObject({
            field: "call_outcome",
            op: "eq",
            value: "booked",
        })
        expect(handoff).toBeTruthy()
        expect(def.nodes).toHaveLength(2)
    })
})

describe("workflow graph — presentational layout (Phase 4)", () => {
    it("setNodePosition persists a position into definition.layout keyed by node id", () => {
        const next = setNodePosition(LINEAR, "sms-1", { x: 512, y: 128 })
        expect(next.layout).toEqual({ "sms-1": { x: 512, y: 128 } })
        // Original untouched.
        expect(LINEAR.layout).toBeUndefined()
    })

    it("setNodePosition merges without dropping other saved positions", () => {
        const a = setNodePosition(LINEAR, "sms-1", { x: 1, y: 2 })
        const b = setNodePosition(a, "wait-1", { x: 3, y: 4 })
        expect(b.layout).toEqual({ "sms-1": { x: 1, y: 2 }, "wait-1": { x: 3, y: 4 } })
    })

    it("definitionToFlow uses a saved layout position when present, else auto-layout", () => {
        const withPos = setNodePosition(LINEAR, "sms-1", { x: 999, y: 777 })
        const { nodes } = definitionToFlow(withPos)
        const sms = nodes.find((n) => n.id === "sms-1")!
        expect(sms.position).toEqual({ x: 999, y: 777 })
        // A node with no saved position still gets an auto-layout coordinate.
        const wait = nodes.find((n) => n.id === "wait-1")!
        expect(typeof wait.position.x).toBe("number")
    })

    it("clearLayout drops manual positions so fallback layout resumes", () => {
        const withPos = setNodePosition(LINEAR, "sms-1", { x: 999, y: 777 })
        const tidied = clearLayout(withPos)
        expect(tidied.layout).toBeUndefined()
        const auto = definitionToFlow(LINEAR).nodes.find((n) => n.id === "sms-1")!
        const tidiedSms = definitionToFlow(tidied).nodes.find((n) => n.id === "sms-1")!
        expect(tidiedSms.position).toEqual(auto.position)
    })

    it("autoLayoutDefinition writes fresh presentational positions for every rendered node", () => {
        const withPos = setNodePosition(BRANCHED, "cond-1", { x: 999, y: 777 })
        const laidOut = autoLayoutDefinition(withPos)
        expect(laidOut.layout?.[TRIGGER_NODE_ID]).toBeTruthy()
        expect(laidOut.layout?.["cond-1"]).toBeTruthy()
        expect(laidOut.layout?.["exit-yes"]).toBeTruthy()
        expect(laidOut.layout?.["exit-no"]).toBeTruthy()
        expect(laidOut.layout?.["cond-1"]).not.toEqual({ x: 999, y: 777 })
        expect(laidOut.nodes).toEqual(withPos.nodes)
        expect(laidOut.entry_node_id).toBe(withPos.entry_node_id)
    })

    it("autoLayoutDefinition keeps true branches above false branches when possible", () => {
        const laidOut = autoLayoutDefinition(BRANCHED)
        expect(laidOut.layout?.["exit-yes"].y).toBeLessThan(laidOut.layout?.["exit-no"].y ?? 0)
    })

    it("layout is purely presentational — it never changes derived edges/semantics", () => {
        const base = definitionToFlow(LINEAR).edges
        const moved = definitionToFlow(setNodePosition(LINEAR, "sms-1", { x: 42, y: 42 })).edges
        // Same edges (source/target/handle), regardless of position.
        const norm = (es: typeof base) =>
            es.map((e) => ({ source: e.source, target: e.target, handle: e.sourceHandle ?? null }))
        expect(norm(moved)).toEqual(norm(base))
    })
})
