import { useState } from "react"
import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import { createNode, outgoing, connectNodes, removeNode } from "@/lib/workflow/graph"
import { validateDefinition } from "@/lib/workflow/validation"
import type { SwitchNode, WorkflowDefinition, WorkflowNode } from "@/types/workflow"

const SWITCH: SwitchNode = {
    type: "switch",
    id: "route",
    subject: "call_outcome",
    cases: [
        {
            label: "Confirmed",
            filter: { kind: "rule", field: "call_outcome", op: "eq", value: "confirmed" },
            next_node_id: "exit-yes",
        },
        {
            label: "Cancelled",
            filter: { kind: "rule", field: "call_outcome", op: "eq", value: "cancelled" },
            next_node_id: "exit-no",
        },
    ],
    default_next_node_id: "exit-review",
}

const DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "route",
    nodes: [
        SWITCH,
        { type: "exit", id: "exit-yes", outcome: "confirmed" },
        { type: "exit", id: "exit-no", outcome: "cancelled" },
        { type: "exit", id: "exit-review", outcome: "staff_handoff" },
    ],
}

function Harness({ onNodeChange }: { onNodeChange: (n: WorkflowNode) => void }) {
    const [def, setDef] = useState(DEF)
    return (
        <StepConfigPanel
            open
            onOpenChange={vi.fn()}
            def={def}
            selectedId="route"
            onNodeChange={(node) => {
                onNodeChange(node)
                setDef((current) => ({
                    ...current,
                    nodes: current.nodes.map((n) => (n.id === node.id ? node : n)),
                }))
            }}
            onDefinitionChange={vi.fn()}
            onTriggerChange={vi.fn()}
            onDeleteNode={vi.fn()}
            onSetEntry={vi.fn()}
        />
    )
}

describe("Switch node", () => {
    it("renders one editable case per branch plus the fallback", () => {
        render(<Harness onNodeChange={vi.fn()} />)

        expect(screen.getByLabelText("Case 1 label")).toHaveValue("Confirmed")
        expect(screen.getByLabelText("Case 2 label")).toHaveValue("Cancelled")
        expect(screen.getByText("Otherwise → go to")).toBeInTheDocument()
    })

    it("adds a case, which becomes a new canvas port", async () => {
        const user = userEvent.setup()
        const onNodeChange = vi.fn()
        render(<Harness onNodeChange={onNodeChange} />)

        await user.click(screen.getByRole("button", { name: /add case/i }))

        const updated = onNodeChange.mock.calls.at(-1)?.[0] as SwitchNode
        expect(updated.cases).toHaveLength(3)
        expect(updated.cases[2].label).toBe("Case 3")
        // One port per case, plus the default.
        expect(outgoing(updated)).toHaveLength(4)
    })

    it("reorders cases, because the first match wins", async () => {
        const user = userEvent.setup()
        const onNodeChange = vi.fn()
        render(<Harness onNodeChange={onNodeChange} />)

        await user.click(screen.getByRole("button", { name: "Move case 2 up" }))

        const updated = onNodeChange.mock.calls.at(-1)?.[0] as SwitchNode
        expect(updated.cases.map((c) => c.label)).toEqual(["Cancelled", "Confirmed"])
    })

    it("keeps the last case, since a switch with none cannot route", async () => {
        const user = userEvent.setup()
        render(<Harness onNodeChange={vi.fn()} />)

        await user.click(screen.getByRole("button", { name: "Remove case 2" }))
        expect(screen.getByRole("button", { name: "Remove case 1" })).toBeDisabled()
    })

    it("edits a case filter through the shared editor", async () => {
        const user = userEvent.setup()
        const onNodeChange = vi.fn()
        render(<Harness onNodeChange={onNodeChange} />)

        const values = screen.getAllByLabelText("Value")
        await user.clear(values[0])
        await user.type(values[0], "reschedule")

        const updated = onNodeChange.mock.calls.at(-1)?.[0] as SwitchNode
        expect(updated.cases[0].filter).toMatchObject({ kind: "rule", value: "reschedule" })
    })
})

describe("Switch graph wiring", () => {
    it("creates a switch with one case and no wiring yet", () => {
        const node = createNode("switch", "s1") as SwitchNode
        expect(node.cases).toHaveLength(1)
        expect(node.default_next_node_id).toBe("")
    })

    it("routes a canvas connection to the port that was dragged", () => {
        const wired = connectNodes(DEF, "route", "exit-review", "case-1")
        const node = wired.nodes[0] as SwitchNode
        expect(node.cases[1].next_node_id).toBe("exit-review")
        // The other case and the default are untouched.
        expect(node.cases[0].next_node_id).toBe("exit-yes")
        expect(node.default_next_node_id).toBe("exit-review")
    })

    it("falls back to the default port for an unknown handle", () => {
        const wired = connectNodes(DEF, "route", "exit-yes", undefined)
        expect((wired.nodes[0] as SwitchNode).default_next_node_id).toBe("exit-yes")
    })

    it("repoints every port when a downstream node is deleted", () => {
        const withStep: WorkflowDefinition = {
            ...DEF,
            nodes: [
                { ...SWITCH, cases: SWITCH.cases.map((c) => ({ ...c, next_node_id: "mid" })), default_next_node_id: "mid" },
                { type: "exit", id: "exit-yes", outcome: "a" },
                { type: "exit", id: "exit-no", outcome: "b" },
                { type: "exit", id: "exit-review", outcome: "c" },
                { type: "wait", id: "mid", wait_for: { type: "time", delay: { delay_type: "duration", duration_seconds: 60 } }, next_node_id: "exit-yes" },
            ],
        }
        const pruned = removeNode(withStep, "mid")
        const node = pruned.nodes.find((n) => n.id === "route") as SwitchNode
        expect(node.cases.every((c) => c.next_node_id === "exit-yes")).toBe(true)
        expect(node.default_next_node_id).toBe("exit-yes")
    })
})

describe("Switch validation", () => {
    it("accepts a well-formed switch", () => {
        expect(validateDefinition(DEF).filter((i) => i.severity === "error")).toEqual([])
    })

    it("flags a duplicate case label, which would make traces ambiguous", () => {
        const broken: WorkflowDefinition = {
            ...DEF,
            nodes: [{ ...SWITCH, cases: [SWITCH.cases[0], { ...SWITCH.cases[1], label: "confirmed" }] }, ...DEF.nodes.slice(1)],
        }
        expect(validateDefinition(broken).map((i) => i.message)).toContain(
            'Switch has two cases labelled "confirmed".',
        )
    })

    it("flags a case whose filter has no value", () => {
        const broken: WorkflowDefinition = {
            ...DEF,
            nodes: [
                {
                    ...SWITCH,
                    cases: [
                        { ...SWITCH.cases[0], filter: { kind: "rule", field: "call_outcome", op: "eq", value: "" } },
                    ],
                },
                ...DEF.nodes.slice(1),
            ],
        }
        expect(
            validateDefinition(broken).some((i) => i.message.includes("has no value")),
        ).toBe(true)
    })

    it("flags a dangling case target", () => {
        const broken: WorkflowDefinition = {
            ...DEF,
            nodes: [
                { ...SWITCH, cases: [{ ...SWITCH.cases[0], next_node_id: "nope" }] },
                ...DEF.nodes.slice(1),
            ],
        }
        expect(
            validateDefinition(broken).some(
                (i) => i.severity === "error" && i.message.includes('Switch case "Confirmed"'),
            ),
        ).toBe(true)
    })
})
