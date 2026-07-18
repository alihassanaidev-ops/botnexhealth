import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import type { WorkflowDefinition } from "@/types/workflow"

const DEF: WorkflowDefinition = {
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
            max_attempts: 1,
        },
        { type: "exit", id: "exit-1", outcome: "done" },
    ],
}

describe("StepConfigPanel voice outcome controls", () => {
    it("exposes wait_for_outcome and inserts a call_outcome branch", async () => {
        const user = userEvent.setup()
        const onDefinitionChange = vi.fn()

        render(
            <StepConfigPanel
                open
                onOpenChange={vi.fn()}
                def={DEF}
                selectedId="voice-1"
                onNodeChange={vi.fn()}
                onDefinitionChange={onDefinitionChange}
                onTriggerChange={vi.fn()}
                onDeleteNode={vi.fn()}
                onSetEntry={vi.fn()}
            />,
        )

        expect(screen.getByText("Wait for voice outcome")).toBeInTheDocument()
        expect(screen.getAllByText(/call_outcome/).length).toBeGreaterThan(0)
        expect(screen.getByText("do_not_call")).toBeInTheDocument()

        await user.click(screen.getByRole("button", { name: /add outcome branch/i }))

        const next = onDefinitionChange.mock.calls[0][0] as WorkflowDefinition
        const condition = next.nodes.find((node) => node.type === "condition")
        expect(condition?.type === "condition" && condition.rules[0].field).toBe("call_outcome")
        expect(next.nodes.some((node) => node.type === "exit" && node.outcome === "staff_handoff")).toBe(true)
    })
})
