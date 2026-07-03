import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas"
import { definitionToFlow } from "@/lib/workflow/graph"
import type { WorkflowDefinition } from "@/types/workflow"

const DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "sms-1",
    nodes: [
        { type: "send_sms", id: "sms-1", body_template: "Hi", next_node_id: "exit-1" },
        { type: "exit", id: "exit-1", outcome: "sent" },
    ],
}

function flow() {
    return definitionToFlow(DEF)
}

describe("WorkflowCanvas — Tidy layout control", () => {
    it("shows a Tidy layout button in editable mode and invokes onTidyLayout on click", async () => {
        const onTidyLayout = vi.fn()
        const { nodes, edges } = flow()
        render(
            <WorkflowCanvas nodes={nodes} edges={edges} editable onTidyLayout={onTidyLayout} />,
        )
        const btn = await screen.findByRole("button", { name: /tidy layout/i })
        await userEvent.click(btn)
        expect(onTidyLayout).toHaveBeenCalledTimes(1)
    })

    it("hides editing affordances in read-only (non-editable) preview mode", () => {
        const { nodes, edges } = flow()
        render(<WorkflowCanvas nodes={nodes} edges={edges} onTidyLayout={vi.fn()} minimal />)
        expect(screen.queryByRole("button", { name: /tidy layout/i })).not.toBeInTheDocument()
    })
})
