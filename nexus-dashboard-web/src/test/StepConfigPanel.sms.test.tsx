import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import type { WorkflowDefinition } from "@/types/workflow"

const DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "sms-1",
    nodes: [
        {
            type: "send_sms",
            id: "sms-1",
            body_template: "Hi",
            include_opt_out_footer: true,
            next_node_id: "exit-1",
        },
        { type: "exit", id: "exit-1", outcome: "done" },
    ],
}

describe("StepConfigPanel SMS footer control", () => {
    it("lets the workflow author disable the automatic STOP footer", () => {
        const onNodeChange = vi.fn()

        render(
            <StepConfigPanel
                open
                onOpenChange={vi.fn()}
                def={DEF}
                selectedId="sms-1"
                onNodeChange={onNodeChange}
                onDefinitionChange={vi.fn()}
                onTriggerChange={vi.fn()}
                onDeleteNode={vi.fn()}
                onSetEntry={vi.fn()}
            />,
        )

        fireEvent.click(screen.getByRole("switch", { name: "Include STOP opt-out footer" }))

        expect(onNodeChange).toHaveBeenCalledWith(
            expect.objectContaining({
                type: "send_sms",
                include_opt_out_footer: false,
            }),
        )
    })
})
