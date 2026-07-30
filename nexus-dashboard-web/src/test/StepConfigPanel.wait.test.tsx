import { useState } from "react"
import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import type { WaitNode, WorkflowDefinition, WorkflowNode } from "@/types/workflow"

const WAIT_NODE: WaitNode = {
    type: "wait",
    id: "wait-1",
    delay: { delay_type: "appointment_relative", offset_seconds: -3600, anchor_field: "appointment_at" },
    next_node_id: "voice-1",
    respect_quiet_hours: true,
}

const BASE_DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "wait-1",
    nodes: [
        WAIT_NODE,
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

function WaitPanelHarness({ onNodeChange }: { onNodeChange: (node: WorkflowNode) => void }) {
    const [def, setDef] = useState(BASE_DEF)
    const handleNodeChange = (node: WorkflowNode) => {
        onNodeChange(node)
        setDef((current) => ({
            ...current,
            nodes: current.nodes.map((existing) => (existing.id === node.id ? node : existing)),
        }))
    }

    return (
        <StepConfigPanel
            open
            onOpenChange={vi.fn()}
            def={def}
            selectedId="wait-1"
            onNodeChange={handleNodeChange}
            onDefinitionChange={vi.fn()}
            onTriggerChange={vi.fn()}
            onDeleteNode={vi.fn()}
            onSetEntry={vi.fn()}
        />
    )
}

describe("StepConfigPanel appointment-relative wait", () => {
    it("offers a custom timing option and stores custom hours as seconds", async () => {
        const user = userEvent.setup()
        const onNodeChange = vi.fn()

        render(<WaitPanelHarness onNodeChange={onNodeChange} />)

        await user.click(screen.getAllByRole("combobox")[1])
        await user.click(await screen.findByRole("option", { name: "Custom offset" }))

        expect(onNodeChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                delay: expect.objectContaining({
                    delay_type: "appointment_relative",
                    offset_seconds: 0,
                }),
            }),
        )

        const customOffset = screen.getByDisplayValue("0")
        await user.clear(customOffset)
        await user.type(customOffset, "0.25")

        expect(onNodeChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                delay: expect.objectContaining({
                    delay_type: "appointment_relative",
                    offset_seconds: 900,
                }),
            }),
        )
    })
})
