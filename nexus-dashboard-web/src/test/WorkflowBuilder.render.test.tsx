import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import WorkflowBuilder from "@/pages/WorkflowBuilder"
import { getWorkflow } from "@/lib/workflow-api"
import type { AutomationWorkflow } from "@/types"

vi.mock("@/lib/workflow-api", () => ({
    getWorkflow: vi.fn(),
    updateWorkflow: vi.fn(),
    pauseWorkflow: vi.fn(),
    resumeWorkflow: vi.fn(),
    archiveWorkflow: vi.fn(),
}))
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const get = getWorkflow as ReturnType<typeof vi.fn>

const WORKFLOW: AutomationWorkflow = {
    id: "wf-1",
    name: "My Reminder Campaign",
    status: "active",
    trigger_type: "appointment_offset",
    definition: {
        schema_version: "1.0",
        trigger: { type: "appointment_offset", offset_hours: -24 },
        entry_node_id: "sms-1",
        nodes: [
            {
                type: "send_sms",
                id: "sms-1",
                body_template: "Hi {{patient_first_name}}",
                next_node_id: "exit-1",
            },
            { type: "exit", id: "exit-1", outcome: "sent" },
        ],
    } as unknown as Record<string, unknown>,
    current_version_id: "v-1",
    created_at: "2026-07-03T00:00:00Z",
    updated_at: "2026-07-03T00:00:00Z",
}

function renderBuilder() {
    return render(
        <MemoryRouter initialEntries={["/institution-admin/campaigns/wf-1/builder"]}>
            <Routes>
                <Route path="/institution-admin/campaigns/:id/builder" element={<WorkflowBuilder />} />
            </Routes>
        </MemoryRouter>,
    )
}

beforeEach(() => {
    get.mockReset()
    localStorage.clear()
})

describe("WorkflowBuilder page (smoke)", () => {
    it("mounts, loads the workflow, and renders the header, palette and validation", async () => {
        get.mockResolvedValue(WORKFLOW)
        renderBuilder()

        // Header shows the editable name.
        expect(await screen.findByDisplayValue("My Reminder Campaign")).toBeInTheDocument()
        // Palette groups render.
        expect(screen.getByText("Channels")).toBeInTheDocument()
        expect(screen.getByText("Control flow")).toBeInTheDocument()
        // Validation panel: the sample workflow is well-formed → all-clear.
        await waitFor(() => {
            expect(screen.getByText(/all checks passed/i)).toBeInTheDocument()
        })
    })

    it("surfaces a validation error for a workflow with no exit", async () => {
        get.mockResolvedValue({
            ...WORKFLOW,
            definition: {
                schema_version: "1.0",
                trigger: { type: "manual" },
                entry_node_id: "sms-1",
                nodes: [{ type: "send_sms", id: "sms-1", body_template: "hi", next_node_id: "sms-1" }],
            } as unknown as Record<string, unknown>,
        })
        renderBuilder()
        expect(await screen.findByText(/at least one Exit step/i)).toBeInTheDocument()
    })
})
