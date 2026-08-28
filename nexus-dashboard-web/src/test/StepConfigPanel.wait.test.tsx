import { useState } from "react"
import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import type { WaitNode, WorkflowDefinition, WorkflowNode } from "@/types/workflow"

const WAIT_NODE: WaitNode = {
    type: "wait",
    id: "wait-1",
    wait_for: {
        type: "time",
        delay: { delay_type: "appointment_relative", offset_seconds: -3600, anchor_field: "appointment_at" },
        respect_quiet_hours: true,
    },
    next_node_id: "voice-1",
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
    it("switches the same Wait node to SMS reply mode", async () => {
        const user = userEvent.setup()
        const onNodeChange = vi.fn()

        render(<WaitPanelHarness onNodeChange={onNodeChange} />)

        await user.click(screen.getAllByRole("combobox")[0])
        await user.click(await screen.findByRole("option", { name: "SMS reply" }))

        expect(screen.queryByText("Include reply key")).not.toBeInTheDocument()
        expect(onNodeChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                type: "wait",
                id: "wait-1",
                wait_for: expect.objectContaining({ type: "sms_reply" }),
            }),
        )
    })

    it("switches the same Wait node to email reply mode", async () => {
        // The backend has executed email-reply waits end-to-end for a while, but
        // the mode was absent from the TypeScript union so the builder could not
        // reach it.
        const user = userEvent.setup()
        const onNodeChange = vi.fn()

        render(<WaitPanelHarness onNodeChange={onNodeChange} />)

        await user.click(screen.getAllByRole("combobox")[0])
        await user.click(await screen.findByRole("option", { name: "Email reply" }))

        expect(onNodeChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                type: "wait",
                id: "wait-1",
                wait_for: expect.objectContaining({
                    type: "email_reply",
                    // A week, not SMS's three days.
                    response_window_seconds: 604800,
                }),
            }),
        )
    })

    it("edits deterministic SMS replies without exposing response mapping JSON", async () => {
        const user = userEvent.setup()
        const onNodeChange = vi.fn()

        render(<WaitPanelHarness onNodeChange={onNodeChange} />)

        await user.click(screen.getAllByRole("combobox")[0])
        await user.click(await screen.findByRole("option", { name: "SMS reply" }))

        const acceptedReplies = screen.getAllByLabelText("Accepted replies")[0]
        expect(acceptedReplies).toHaveValue("YES, Y")
        expect(screen.queryByText(/context_updates/)).not.toBeInTheDocument()

        await user.clear(acceptedReplies)
        await user.type(acceptedReplies, "CONFIRM, YES")
        await user.tab()

        expect(onNodeChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                wait_for: expect.objectContaining({
                    response_mappings: expect.arrayContaining([
                        expect.objectContaining({
                            tokens: ["CONFIRM", "YES"],
                            context_updates: { sms_reply: "yes" },
                        }),
                    ]),
                }),
            }),
        )

        await user.click(screen.getByRole("combobox", { name: "Action for reply rule 1" }))
        await user.click(await screen.findByRole("option", { name: "Create staff handoff" }))

        expect(onNodeChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                wait_for: expect.objectContaining({
                    response_mappings: expect.arrayContaining([
                        expect.objectContaining({
                            tokens: ["CONFIRM", "YES"],
                            handoff_reason: "sms_reply_requires_staff",
                        }),
                    ]),
                }),
            }),
        )
    })

    it("offers a custom timing option and stores custom hours as seconds", async () => {
        const user = userEvent.setup()
        const onNodeChange = vi.fn()

        render(<WaitPanelHarness onNodeChange={onNodeChange} />)

        const timingSelect = screen.getAllByRole("combobox").find(
            (element) => element.textContent?.includes("1 hour before appointment"),
        )
        expect(timingSelect).toBeDefined()
        await user.click(timingSelect!)
        await user.click(await screen.findByRole("option", { name: "Custom offset" }))

        expect(onNodeChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                wait_for: expect.objectContaining({
                    delay: expect.objectContaining({
                        delay_type: "appointment_relative",
                        offset_seconds: 0,
                    }),
                }),
            }),
        )

        const customOffset = screen.getByDisplayValue("0")
        await user.clear(customOffset)
        await user.type(customOffset, "0.25")

        expect(onNodeChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                wait_for: expect.objectContaining({
                    delay: expect.objectContaining({
                        delay_type: "appointment_relative",
                        offset_seconds: 900,
                    }),
                }),
            }),
        )
    })
})
