import { useState } from "react"
import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import { createNode, outgoing, connectNodes, removeNode } from "@/lib/workflow/graph"
import { validateDefinition } from "@/lib/workflow/validation"
import type { SplitNode, WorkflowDefinition, WorkflowNode } from "@/types/workflow"

const SPLIT: SplitNode = {
    type: "split",
    id: "ab",
    subject: "Reminder wording",
    branches: [
        { label: "Variant A", weight: 50, next_node_id: "exit-a" },
        { label: "Variant B", weight: 50, next_node_id: "exit-b" },
    ],
}

const DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "ab",
    nodes: [
        SPLIT,
        { type: "exit", id: "exit-a", outcome: "sent_a" },
        { type: "exit", id: "exit-b", outcome: "sent_b" },
    ],
}

function Harness({ onNodeChange }: { onNodeChange: (n: WorkflowNode) => void }) {
    const [def, setDef] = useState(DEF)
    return (
        <StepConfigPanel
            open
            onOpenChange={vi.fn()}
            def={def}
            selectedId="ab"
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

describe("Split node editor", () => {
    it("renders one editable branch per arm and the running total", () => {
        render(<Harness onNodeChange={vi.fn()} />)

        expect(screen.getByLabelText("Branch 1 label")).toHaveValue("Variant A")
        expect(screen.getByLabelText("Branch 2 percentage")).toHaveValue(50)
        expect(screen.getByText("100% of 100%")).toBeInTheDocument()
    })

    it("has no generic Next step field — its arms are the only forward pointers", () => {
        render(<Harness onNodeChange={vi.fn()} />)
        expect(screen.queryByText("Next step")).not.toBeInTheDocument()
    })

    it("re-splits evenly when an arm is added, so the node stays publishable", async () => {
        const user = userEvent.setup()
        const onNodeChange = vi.fn()
        render(<Harness onNodeChange={onNodeChange} />)

        await user.click(screen.getByRole("button", { name: /add branch/i }))

        const updated = onNodeChange.mock.calls.at(-1)![0] as SplitNode
        expect(updated.branches).toHaveLength(3)
        // 34/33/33 — the remainder goes to the first arm so the total is exactly 100.
        expect(updated.branches.map((b) => b.weight)).toEqual([34, 33, 33])
        expect(updated.branches.reduce((sum, b) => sum + b.weight, 0)).toBe(100)
    })

    it("re-splits evenly when an arm is removed", async () => {
        const user = userEvent.setup()
        const onNodeChange = vi.fn()
        render(<Harness onNodeChange={onNodeChange} />)

        await user.click(screen.getByRole("button", { name: /add branch/i }))
        await user.click(screen.getByRole("button", { name: /remove branch 3/i }))

        const updated = onNodeChange.mock.calls.at(-1)![0] as SplitNode
        expect(updated.branches.map((b) => b.weight)).toEqual([50, 50])
    })

    it("will not let the author drop below two arms", () => {
        render(<Harness onNodeChange={vi.fn()} />)
        expect(screen.getByRole("button", { name: /remove branch 1/i })).toBeDisabled()
    })
})

describe("Split node graph wiring", () => {
    it("exposes one canvas port per arm, labelled with its share", () => {
        expect(outgoing(SPLIT)).toEqual([
            { targetId: "exit-a", handle: "branch-0", label: "Variant A 50%" },
            { targetId: "exit-b", handle: "branch-1", label: "Variant B 50%" },
        ])
    })

    it("starts as an even two-way split so a new node is already valid", () => {
        const node = createNode("split", "ab-1") as SplitNode
        expect(node.branches.map((b) => b.weight)).toEqual([50, 50])
    })

    it("connects a dragged edge to the arm its handle names", () => {
        const next = connectNodes(DEF, "ab", "exit-b", "branch-0")
        const node = next.nodes.find((n) => n.id === "ab") as SplitNode
        expect(node.branches[0].next_node_id).toBe("exit-b")
        expect(node.branches[1].next_node_id).toBe("exit-b")
    })

    it("ignores an edge dropped with no recognisable arm handle", () => {
        // No fallback port to absorb it, so the safe move is to change nothing
        // rather than silently rewire an arm the user did not drag from.
        expect(connectNodes(DEF, "ab", "exit-b", "default")).toBe(DEF)
    })

    it("repoints arms that pointed at a deleted step", () => {
        const next = removeNode(DEF, "exit-a")
        const node = next.nodes.find((n) => n.id === "ab") as SplitNode
        expect(node.branches[0].next_node_id).toBe("")
    })
})

describe("Split node validation", () => {
    const withBranches = (branches: SplitNode["branches"]): WorkflowDefinition => ({
        ...DEF,
        nodes: DEF.nodes.map((n) => (n.id === "ab" ? { ...SPLIT, branches } : n)),
    })

    const messages = (def: WorkflowDefinition) =>
        validateDefinition(def).map((issue) => issue.message)

    it("accepts an even split", () => {
        expect(messages(DEF)).toEqual([])
    })

    it("rejects weights that do not add up to 100", () => {
        const issues = messages(
            withBranches([
                { label: "A", weight: 30, next_node_id: "exit-a" },
                { label: "B", weight: 30, next_node_id: "exit-b" },
            ]),
        )
        expect(issues).toContain("Split weights add up to 60%, not 100%.")
    })

    it("rejects two arms sharing a label, which would merge them in the report", () => {
        const issues = messages(
            withBranches([
                { label: "A", weight: 50, next_node_id: "exit-a" },
                { label: "a", weight: 50, next_node_id: "exit-b" },
            ]),
        )
        expect(issues).toContain('Split has two branches labelled "a".')
    })

    it("rejects an unconnected arm", () => {
        const issues = messages(
            withBranches([
                { label: "A", weight: 50, next_node_id: "exit-a" },
                { label: "B", weight: 50, next_node_id: "" },
            ]),
        )
        expect(issues.some((m) => m.includes('Split branch "B"'))).toBe(true)
    })
})
