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

describe("WorkflowCanvas — Auto layout control", () => {
    it("shows the Chair Flow state in a post-op trigger card", () => {
        const postOp: WorkflowDefinition = {
            ...DEF,
            trigger: {
                type: "appointment_state_changed",
                status_ids: [],
                confirmed: null,
                preconfirmed: null,
                flow_states: ["Completed"],
                max_followup_delay_hours: 72,
                campaign_goal: "post_op_followup",
            },
        }
        const { nodes, edges } = definitionToFlow(postOp)

        render(<WorkflowCanvas nodes={nodes} edges={edges} />)

        expect(screen.getByText("Flow: Completed")).toBeInTheDocument()
    })

    it("shows an Auto layout button in editable mode and invokes onAutoLayout on click", async () => {
        const onAutoLayout = vi.fn()
        const { nodes, edges } = flow()
        render(
            <WorkflowCanvas nodes={nodes} edges={edges} editable onAutoLayout={onAutoLayout} />,
        )
        const btn = await screen.findByRole("button", { name: /auto layout/i })
        await userEvent.click(btn)
        expect(onAutoLayout).toHaveBeenCalledTimes(1)
    })

    it("hides editing affordances in read-only (non-editable) preview mode", () => {
        const { nodes, edges } = flow()
        render(<WorkflowCanvas nodes={nodes} edges={edges} onAutoLayout={vi.fn()} minimal />)
        expect(screen.queryByRole("button", { name: /auto layout/i })).not.toBeInTheDocument()
    })
})
