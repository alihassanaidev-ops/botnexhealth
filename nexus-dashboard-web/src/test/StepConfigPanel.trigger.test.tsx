import { useState } from "react"
import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import { TRIGGER_NODE_ID } from "@/lib/workflow/graph"
import type { WorkflowDefinition, WorkflowTrigger } from "@/types/workflow"

const POST_OP_DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: {
        type: "appointment_state_changed",
        status_ids: [],
        confirmed: null,
        preconfirmed: null,
        flow_states: ["Completed"],
        max_followup_delay_hours: 72,
        campaign_goal: "post_op_followup",
    },
    entry_node_id: "exit-1",
    nodes: [{ type: "exit", id: "exit-1", outcome: "done" }],
}

function TriggerPanelHarness({ onTriggerChange }: { onTriggerChange: (trigger: WorkflowTrigger) => void }) {
    const [def, setDef] = useState(POST_OP_DEF)
    const handleTriggerChange = (trigger: WorkflowTrigger) => {
        onTriggerChange(trigger)
        setDef((current) => ({ ...current, trigger }))
    }

    return (
        <StepConfigPanel
            open
            onOpenChange={vi.fn()}
            def={def}
            selectedId={TRIGGER_NODE_ID}
            onNodeChange={vi.fn()}
            onDefinitionChange={vi.fn()}
            onTriggerChange={handleTriggerChange}
            onDeleteNode={vi.fn()}
            onSetEntry={vi.fn()}
        />
    )
}

describe("StepConfigPanel appointment-state trigger", () => {
    it("exposes the Completed Chair Flow matcher and post-op deadline", () => {
        render(<TriggerPanelHarness onTriggerChange={vi.fn()} />)

        expect(screen.getByLabelText("Chair Flow states")).toHaveValue("Completed")
        expect(screen.getByLabelText("Latest follow-up window (hours after flow change)")).toHaveValue(72)
        expect(screen.getByText("GoTracker status (optional AND filter)")).toBeInTheDocument()
        expect(screen.getByText("Any status")).toBeInTheDocument()
        expect(screen.getAllByText("Do not restrict")).toHaveLength(2)
        expect(screen.getByText(/combined with it using AND/)).toBeInTheDocument()
    })

    it("writes edited Chair Flow states and deadline back to the trigger definition", async () => {
        const onTriggerChange = vi.fn()
        const user = userEvent.setup()
        render(<TriggerPanelHarness onTriggerChange={onTriggerChange} />)

        const flowStates = screen.getByLabelText("Chair Flow states")
        await user.clear(flowStates)
        await user.type(flowStates, "Reception, Completed")
        expect(flowStates).toHaveValue("Reception, Completed")
        expect(onTriggerChange).toHaveBeenLastCalledWith(
            expect.objectContaining({ flow_states: ["Reception", "Completed"] }),
        )

        fireEvent.change(screen.getByLabelText("Latest follow-up window (hours after flow change)"), {
            target: { value: "48" },
        })
        expect(onTriggerChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                flow_states: ["Reception", "Completed"],
                max_followup_delay_hours: 48,
            }),
        )
    })
})
