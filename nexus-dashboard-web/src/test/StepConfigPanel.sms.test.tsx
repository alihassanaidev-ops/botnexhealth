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

    it("shows only the agent profile and next step for Retell SMS", () => {
        const definition: WorkflowDefinition = {
            schema_version: "1.0",
            trigger: { type: "inbound_message", channels: ["sms"] },
            entry_node_id: "chat-1",
            nodes: [
                {
                    type: "retell_sms_conversation",
                    id: "chat-1",
                    chat_profile_id: "profile-1",
                    next_node_id: "exit-1",
                },
                { type: "exit", id: "exit-1", outcome: "done" },
            ],
        }

        render(
            <StepConfigPanel
                open
                onOpenChange={vi.fn()}
                def={definition}
                selectedId="chat-1"
                onNodeChange={vi.fn()}
                onDefinitionChange={vi.fn()}
                onTriggerChange={vi.fn()}
                onDeleteNode={vi.fn()}
                onSetEntry={vi.fn()}
                retellSmsProfiles={[{
                    id: "profile-1",
                    institution_id: "institution-1",
                    location_id: "location-1",
                    retell_agent_id: null,
                    agent_version: null,
                    display_name: "Appointment assistant",
                    purpose: "appointments",
                    allowed_tools: [],
                    is_active: true,
                    config: null,
                    created_at: "2026-08-25T00:00:00Z",
                    updated_at: "2026-08-25T00:00:00Z",
                }]}
            />,
        )

        expect(screen.getByText("AI SMS agent profile")).toBeInTheDocument()
        expect(screen.getByText("Next step")).toBeInTheDocument()
        expect(screen.queryByText("Inactivity timeout")).not.toBeInTheDocument()
        expect(screen.queryByText("Human handoff phrases")).not.toBeInTheDocument()
        expect(screen.queryByText("Additional dynamic variables")).not.toBeInTheDocument()
    })
})
